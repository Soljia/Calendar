package org.fossify.calendar.models

data class CalDAVCalendar(
    val id: Int,
    val displayName: String,
    val accountName: String,
    val accountType: String,
    val ownerName: String,
    var color: Int,
    val accessLevel: Int,
    val maxReminders: Int = Int.MAX_VALUE
) {
    fun canWrite() = accessLevel >= 500

    fun getFullTitle() = "$displayName ($accountName)"
}
