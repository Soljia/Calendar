package org.fossify.calendar.helpers

import androidx.room.TypeConverter
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import org.fossify.calendar.models.Attendee
import org.fossify.calendar.models.Reminder

class Converters {
    private val gson = Gson()
    private val stringType = object : TypeToken<List<String>>() {}.type
    private val attendeeType = object : TypeToken<List<Attendee>>() {}.type
    private val reminderType = object : TypeToken<List<Reminder>>() {}.type

    @TypeConverter
    fun jsonToStringList(value: String?): List<String> {
        val newValue = if (!value.isNullOrEmpty() && !value.startsWith("[")) {
            "[$value]"
        } else {
            value
        }

        return try {
            gson.fromJson(newValue, stringType)
        } catch (e: Exception) {
            emptyList()
        }
    }

    @TypeConverter
    fun stringListToJson(list: List<String>) = gson.toJson(list)

    @TypeConverter
    fun attendeeListToJson(list: List<Attendee>): String = gson.toJson(list)

    @TypeConverter
    fun jsonToAttendeeList(value: String): List<Attendee> {
        if (value.isEmpty()) {
            return emptyList()
        }

        return try {
            gson.fromJson<ArrayList<Attendee>>(value, attendeeType) ?: ArrayList()
        } catch (e: Exception) {
            emptyList()
        }
    }

    @TypeConverter
    fun reminderListToJson(list: List<Reminder>): String = gson.toJson(list)

    @TypeConverter
    fun jsonToReminderList(value: String): List<Reminder> {
        if (value.isEmpty()) {
            return emptyList()
        }

        return try {
            gson.fromJson<ArrayList<Reminder>>(value, reminderType) ?: ArrayList()
        } catch (e: Exception) {
            emptyList()
        }
    }
}
