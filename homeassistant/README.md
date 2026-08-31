# HVAC Window Advisor

A Home Assistant automation that tells you when to open the windows so the
weather does your heating or cooling for you — and when to shut them again.

It notifies you when the **outdoor feels-like temperature** will stay on the
helpful side of your **HVAC set point** for the **next 8 hours** (configurable),
and sends a follow-up when that stops being true.

| Advisor state | Meaning |
| --- | --- |
| `open_for_cooling` | Feels-like stays **below** the cooling set point for the whole window — open up, save the AC. |
| `open_for_heating` | Feels-like stays **above** the heating set point for the whole window — open up, save the furnace. |
| `keep_closed` | Not favourable, or not favourable for long enough. |
| `unknown` | No hourly forecast reaching the end of the window. |

The look-ahead is **strict**: every forecast hour inside the window has to be on
the right side of the set point. One warm hour in the middle of an otherwise
cool night suppresses the notification — which is the point, since you should
not be opening windows for a benefit that evaporates in an hour.

## Requirements

- Home Assistant **2024.3+** (for `weather.get_forecasts` and `response_variable`).
- A weather entity that provides **hourly** forecasts — Met.no, Open-Meteo,
  AccuWeather, Pirate Weather, OpenWeatherMap, and most others do.
- A `climate` entity with a set point.

## Install

1. Copy `packages/hvac_window_advisor.yaml` into your HA config at
   `<config>/packages/hvac_window_advisor.yaml`.

2. Make sure `configuration.yaml` loads the packages directory:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

3. Edit the three placeholders in the package — every one is flagged with a
   `# <-- EDIT` comment:

   | Placeholder | Replace with | Where |
   | --- | --- | --- |
   | `climate.hvac` | your thermostat entity | 3 triggers + the `climate_entity` variable |
   | `weather.forecast_home` | your hourly-capable weather entity | the `weather_entity` variable |
   | `notify.mobile_app_your_phone` | your notify service | the `notify_service` variable in each automation |

   The `sed` below rewrites the header comment too, which is harmless.

   ```bash
   sed -i \
     -e 's/climate\.hvac/climate.living_room/g' \
     -e 's/weather\.forecast_home/weather.met_no/g' \
     -e 's/notify\.mobile_app_your_phone/notify.mobile_app_pixel/g' \
     packages/hvac_window_advisor.yaml
   ```

4. **Developer Tools → YAML → Check configuration**, then restart Home
   Assistant (a new package needs a full restart the first time).

5. Turn on `input_boolean.window_advisor_enabled`. The advisor sensor
   itself always runs; this toggle only gates the notifications.

## Tuning

Five helpers appear in Settings → Devices & Services → Helpers, all adjustable
without touching YAML:

| Helper | Default | What it does |
| --- | --- | --- |
| `input_boolean.window_advisor_enabled` | — | Master on/off for the two notifications. |
| `input_boolean.window_advisor_require_hvac_demand` | — | See below. |
| `input_number.window_advisor_forecast_hours` | 8 | The look-ahead window. |
| `input_number.window_advisor_margin` | 1° | How far past the set point it has to be before it is worth opening a window. |
| `input_number.window_advisor_hysteresis` | 0.5° | Extra slack applied only while already advising "open", so a forecast sitting on the threshold does not flip the advice every 15 minutes. |

`initial:` on the three numbers means they reset to the defaults above on every
restart. Delete those lines if you would rather your tweaks persist.

### The HVAC demand gate

`window_advisor_require_hvac_demand` is worth understanding, because it is the
difference between a useful automation and one you mute after two days.

- **ON** — only advise when the HVAC would actually be running: the indoor
  temperature is on the wrong side of the set point *and* the thermostat is in a
  mode that could act on it (`cool`/`heat`/`heat_cool`/`auto`). This is what
  stops you being told to "open for cooling" at 3 a.m. in January, when it is
  −5 °C outside, well below your 21 °C set point, and technically "below the set
  point for the next 8 hours".
- **OFF** — the literal reading: advise whenever the forecast is on the right
  side of the set point, regardless of what the house or thermostat is doing.

Indoor temperature comes from the thermostat's `current_temperature`. If you
have a better-placed thermometer, set `indoor_entity` in the package's first
`variables:` block to that sensor.

## How "feels like" is computed

If the weather integration publishes `apparent_temperature` in its hourly
forecast, that value is used as-is. Most integrations do not, so the package
computes the **Steadman apparent temperature** — the same "feels like" the
Australian Bureau of Meteorology and Open-Meteo use — from forecast
temperature, relative humidity and wind speed:

```
AT = Ta + 0.33 × e − 0.70 × ws − 4.00          (Ta in °C, ws in m/s at 10 m)
e  = RH/100 × 6.105 × exp(17.27 × Ta / (237.7 + Ta))
```

This formula is continuous across the whole range. A heat-index/wind-chill pair
would be undefined between roughly 10 °C and 27 °C — exactly the band where most
of the interesting window-opening weather lives.

°C/°F and the wind-speed unit are read from the weather entity's own
`temperature_unit` and `wind_speed_unit` attributes, so no unit configuration is
needed. The `feels_like_estimated` attribute is `true` when the values were
derived here rather than published by the integration.

## Entities created

`sensor.window_advisor` carries the full picture as attributes: `reason`,
`hvac_mode`, `cooling_setpoint`, `heating_setpoint`, `indoor_temperature`,
`feels_like_now` / `_min` / `_max`, `temperature_unit`, `forecast_hours`,
`margin`, `hysteresis`, `forecast_covers_window`, `feels_like_estimated`, and
`hourly_feels_like` (the per-hour series it decided on).

The sensor re-evaluates every 15 minutes, on HA start, when you change a helper,
and immediately when you change a set point.

A minimal Lovelace card:

```yaml
type: entities
entities:
  - entity: sensor.window_advisor
    secondary_info: last-changed
  - type: attribute
    entity: sensor.window_advisor
    attribute: reason
    name: Why
  - entity: input_boolean.window_advisor_enabled
  - entity: input_number.window_advisor_forecast_hours
  - entity: input_number.window_advisor_margin
```

## Troubleshooting

**Sensor is stuck on `unknown`.** The forecast does not reach the end of the
window. Check the `reason` attribute, then in Developer Tools → Actions run
`weather.get_forecasts` with `type: hourly` against your weather entity. If it
returns daily-only data or fewer hours than your window, either the integration
does not support hourly forecasts or you need a shorter
`window_advisor_forecast_hours`. Three-hourly forecasts are fine — coverage is
measured from the forecast's own step size, not from a count of entries.

**Never fires even though the weather looks right.** Check
`forecast_covers_window` and `feels_like_max` on the sensor. Remember the
comparison is against *feels-like*, not dry-bulb: humidity and wind routinely
move it several degrees. Then check the demand gate — with it on, a thermostat
in `off` mode never produces advice.

**Notifications fire but the house gets worse.** The set point band is doing
what you asked; raise `window_advisor_margin` so marginal opportunities are
ignored.

**Advice flip-flops.** Raise `window_advisor_hysteresis`.

## Note on this repository

This lives alongside a fork of the Fossify Calendar Android app. It shares no
code with the app and is not built by Gradle — it is standalone Home Assistant
configuration, kept here for version control.
