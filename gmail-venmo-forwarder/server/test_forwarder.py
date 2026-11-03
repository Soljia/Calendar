import email
import email.policy
import unittest
from email.message import EmailMessage
from unittest import mock

import forwarder as f

CFG = f.Config(
    address="me@gmail.com",
    app_password="x",
    forward_to=["a@x.com", "b@x.com", "c@x.com", "d@x.com"],
    required_terms=["paid you", "venmo"],
)


def venmo_email(subject="Jane Doe paid you $25.00", html_body=None, plain=None, sender="Venmo <venmo@venmo.com>"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "me@gmail.com"
    msg["Subject"] = subject
    msg["Date"] = "Tue, 03 Nov 2026 10:00:00 -0500"
    msg.set_content(plain or "See details in the Venmo app.")
    if html_body is not None:
        msg.add_alternative(html_body, subtype="html")
    # Round-trip through bytes like a real IMAP fetch.
    return email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)


class MatchTests(unittest.TestCase):
    def test_matches_subject_and_body(self):
        self.assertTrue(f.should_forward(venmo_email(), CFG))

    def test_case_insensitive_and_html_split_by_tags(self):
        msg = venmo_email(subject="Payment", plain="hello",
                          html_body="<p><b>Jane</b> PAID&nbsp;<i>you</i> $5 on <span>VENMO</span></p>")
        self.assertTrue(f.should_forward(msg, CFG))

    def test_requires_all_terms(self):
        self.assertFalse(f.should_forward(venmo_email(subject="Jane paid you", plain="cash"), CFG))
        self.assertFalse(f.should_forward(venmo_email(subject="Venmo statement", plain="monthly"), CFG))

    def test_skips_own_mail_and_loop_header(self):
        self.assertFalse(f.should_forward(venmo_email(sender="Me <ME@gmail.com>"), CFG))
        fwd = f.build_forward(venmo_email(), CFG)
        again = email.message_from_bytes(fwd.as_bytes(), policy=email.policy.default)
        self.assertFalse(f.should_forward(again, CFG))


class SenderFilterTests(unittest.TestCase):
    def test_allowed_senders(self):
        import dataclasses
        cfg = dataclasses.replace(CFG, allowed_senders=("venmo.com",))
        self.assertTrue(f.should_forward(venmo_email(), cfg))
        self.assertTrue(f.should_forward(venmo_email(sender="x@email.venmo.com"), cfg))
        self.assertFalse(f.should_forward(venmo_email(sender="scam@notvenmo.com"), cfg))


class ForwardTests(unittest.TestCase):
    def test_builds_forward_to_all_recipients(self):
        fwd = f.build_forward(venmo_email(html_body="<p>Jane paid you</p>"), CFG)
        self.assertEqual(fwd["To"], "a@x.com, b@x.com, c@x.com, d@x.com")
        self.assertEqual(fwd["Subject"], "Fwd: Jane Doe paid you $25.00")
        self.assertIn("Forwarded message", fwd.get_body(("plain",)).get_content())
        self.assertIn("<p>Jane paid you</p>", fwd.get_body(("html",)).get_content())

    def test_catch_up_query(self):
        self.assertEqual(f.catch_up_query(CFG), '"paid you" venmo newer_than:2d -label:Venmo-Forwarded')


class ProcessTests(unittest.TestCase):
    def test_forwards_matching_then_labels_and_skips_rest(self):
        good = venmo_email().as_bytes()
        other = venmo_email(subject="Lunch?", plain="nope").as_bytes()
        done = venmo_email().as_bytes()
        client = mock.Mock()
        client.fetch.return_value = {
            1: {b"BODY[]": good, b"X-GM-LABELS": (b"\\Inbox",)},
            2: {b"BODY[]": other, b"X-GM-LABELS": ()},
            3: {b"BODY[]": done, b"X-GM-LABELS": (b'"Venmo-Forwarded"',)},
        }
        with mock.patch.object(f, "send") as send:
            f.process_uids(client, CFG, [3, 1, 2])
        send.assert_called_once()
        client.add_gmail_labels.assert_called_once_with([1], ["Venmo-Forwarded"])

    def test_send_failure_does_not_label(self):
        client = mock.Mock()
        client.fetch.return_value = {1: {b"BODY[]": venmo_email().as_bytes(), b"X-GM-LABELS": ()}}
        with mock.patch.object(f, "send", side_effect=OSError("smtp down")):
            with self.assertRaises(OSError):
                f.process_uids(client, CFG, [1])
        client.add_gmail_labels.assert_not_called()


if __name__ == "__main__":
    unittest.main()
