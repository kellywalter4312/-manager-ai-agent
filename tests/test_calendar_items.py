"""Generalized Personal Organization/Calendar checkpoint - the pure
extraction/lifecycle module, unit-tested the same way every sibling
extractor already is: no runtime needed, no Brain, a real
``EphemeralMemory`` where retrieval/supersession semantics need proving.
"""

from __future__ import annotations

from datetime import date, time

from src.services.memory.calendar_items import (
    STATUS_CANCELLED,
    STATUS_SCHEDULED,
    CalendarItemCandidate,
    Recurrence,
    all_current_calendar_items,
    build_calendar_item_record,
    calendar_add_intent,
    calendar_cancel_intent,
    calendar_reschedule_intent,
    calendar_retrieve_intent,
    current_item_for_key,
    current_series_for_topic,
    extract_calendar_item,
    extract_cancel_target,
    extract_period_query,
    extract_reschedule,
    find_conflicts,
    find_time_range,
    find_weekdays,
    next_occurrences,
    next_weekday_occurrence,
    parse_calendar_date,
)
from src.services.memory.ephemeral import EphemeralMemory

TODAY = date(2026, 9, 8)  # a Tuesday


class TestFindWeekdays:
    def test_a_range(self):
        days, span = find_weekdays("Mon-Fri 9-3 IT")
        assert days == (0, 1, 2, 3, 4)
        assert span == (0, 7)

    def test_a_wrapping_range(self):
        days, _ = find_weekdays("Fri-Mon shift")
        assert days == (4, 5, 6, 0)

    def test_a_list(self):
        days, _ = find_weekdays("Monday and Wednesday yoga")
        assert days == (0, 2)

    def test_a_single_day(self):
        days, _ = find_weekdays("every Friday at 5pm drinks")
        assert days == (4,)

    def test_no_weekday_present(self):
        assert find_weekdays("I have a dentist appointment") is None


class TestFindTimeRange:
    def test_bare_descending_range_crosses_noon(self):
        start, end, _ = find_time_range("9-3")
        assert start == time(9, 0) and end == time(15, 0)

    def test_bare_ascending_range_low_hour_is_afternoon(self):
        start, end, _ = find_time_range("2-4")
        assert start == time(14, 0) and end == time(16, 0)

    def test_bare_ascending_range_high_hour_is_morning(self):
        start, end, _ = find_time_range("9-11")
        assert start == time(9, 0) and end == time(11, 0)

    def test_explicit_am_pm(self):
        start, end, _ = find_time_range("9am-3pm")
        assert start == time(9, 0) and end == time(15, 0)

    def test_single_time_with_at(self):
        start, end, _ = find_time_range("at 2:30pm")
        assert start == time(14, 30) and end is None

    def test_no_time_present(self):
        assert find_time_range("no numbers here") is None


class TestParseCalendarDate:
    def test_today_and_tomorrow(self):
        assert parse_calendar_date("today", today=TODAY) == TODAY
        assert parse_calendar_date("tomorrow", today=TODAY) == date(2026, 9, 9)

    def test_bare_weekday_is_the_next_occurrence(self):
        # TODAY is a Tuesday; "Friday" should be the same week's Friday.
        assert parse_calendar_date("Friday", today=TODAY) == date(2026, 9, 11)

    def test_full_date_with_year(self):
        assert parse_calendar_date("2026-09-10", today=TODAY) == date(2026, 9, 10)

    def test_month_day_without_year_rolls_to_next_year_if_passed(self):
        assert parse_calendar_date("January 1", today=TODAY) == date(2027, 1, 1)

    def test_unrecognised_text_is_honestly_none(self):
        assert parse_calendar_date("sometime soon", today=TODAY) is None


class TestNextWeekdayOccurrence:
    def test_same_day_returns_today(self):
        assert next_weekday_occurrence(TODAY.weekday(), today=TODAY) == TODAY

    def test_wraps_to_next_week(self):
        # TODAY is Tuesday (1); asking for Monday (0) must be next week.
        assert next_weekday_occurrence(0, today=TODAY) == date(2026, 9, 14)


class TestExtractCalendarItemAdd:
    def test_dated_appointment_with_time(self):
        c = extract_calendar_item("I have a dentist appointment on 2026-09-11 at 3pm")
        assert c is not None
        assert c.title == "dentist appointment"
        assert c.start_date == date(2026, 9, 11)
        assert c.start_time == time(15, 0)

    def test_add_marker_with_label_prefix(self):
        c = extract_calendar_item("Add an event: team meeting on 2026-09-10 at 10am")
        assert c is not None
        assert c.title == "team meeting"
        assert c.start_date == date(2026, 9, 10)

    def test_recurring_every_weekday_at_time(self):
        c = extract_calendar_item("every Monday at 10am I have a study group")
        assert c is not None
        assert c.title == "a study group"
        assert c.recurrence.frequency == "weekly"
        assert c.recurrence.days_of_week == (0,)
        assert c.start_time == time(10, 0)

    def test_recurring_weekday_range_leading(self):
        c = extract_calendar_item("Mon-Fri 9-3 I have IT")
        assert c is not None
        assert c.title == "IT"
        assert c.recurrence.days_of_week == (0, 1, 2, 3, 4)
        assert c.start_time == time(9, 0) and c.end_time == time(15, 0)

    def test_recurring_label_shape_falls_back_to_label_as_title(self):
        c = extract_calendar_item("This is my new IT schedule: Mon-Fri 9am-3pm")
        assert c is not None
        assert c.title == "IT schedule"
        assert c.recurrence.days_of_week == (0, 1, 2, 3, 4)

    def test_no_false_positive_on_ordinary_conversation(self):
        for text in ("thanks, sounds good", "I am currently learning Rust", "what do you think?"):
            assert extract_calendar_item(text) is None, text

    def test_mentioning_a_weekday_mid_sentence_is_not_a_schedule(self):
        assert extract_calendar_item("I visited Paris and Monday was rainy") is None

    def test_calendar_add_intent_matches_extraction(self):
        assert calendar_add_intent("I have a dentist appointment on Friday at 3pm")
        assert not calendar_add_intent("thanks, sounds good")


class TestCancelAndReschedule:
    def test_cancel_target(self):
        assert calendar_cancel_intent("cancel my dentist appointment")
        assert extract_cancel_target("cancel my dentist appointment") == "dentist appointment"

    def test_reschedule_to_a_new_date_and_time(self):
        assert calendar_reschedule_intent("move my team meeting to Friday at 2pm")
        result = extract_reschedule("move my team meeting to Friday at 2pm", today=TODAY)
        assert result is not None
        target, new_date, new_start, new_end = result
        assert target == "team meeting"
        assert new_date == next_weekday_occurrence(4, today=TODAY)
        assert new_start == time(14, 0)

    def test_reschedule_to_a_bare_time(self):
        result = extract_reschedule("move my meeting to 4pm", today=TODAY)
        assert result is not None
        target, new_date, new_start, new_end = result
        assert target == "meeting"
        assert new_date is None
        assert new_start == time(16, 0)

    def test_no_false_positive(self):
        assert not calendar_reschedule_intent("thanks, sounds good")
        assert extract_reschedule("thanks, sounds good") is None


class TestRetrieveIntent:
    def test_bare_listing(self):
        assert calendar_retrieve_intent("what's on my calendar")
        assert extract_period_query("what's on my calendar") == ""

    def test_period_query(self):
        assert calendar_retrieve_intent("what's coming up this week")
        assert extract_period_query("what's coming up this week") == "week"

    def test_no_false_positive(self):
        assert not calendar_retrieve_intent("thanks, sounds good")


class TestBuildCalendarItemRecord:
    def test_record_carries_structured_fields(self):
        candidate = CalendarItemCandidate(
            title="Dentist", start_date=date(2026, 9, 11), start_time=time(15, 0),
        )
        record = build_calendar_item_record(candidate, agent_name="Alfred")
        assert record.kind == "calendar_item"
        assert record.context["title"] == "Dentist"
        assert record.context["start_date"] == "2026-09-11"
        assert record.context["start_time"] == "15:00:00"
        assert record.context["status"] == STATUS_SCHEDULED
        assert record.context["noted_by"] == "Alfred"
        item_key = record.context["item_key"]
        assert f"calendar_item_key:{item_key}" in record.tags


class TestRetrievalLifecycleAgainstRealMemory:
    def test_a_standalone_item_is_retrievable(self):
        memory = EphemeralMemory()
        candidate = CalendarItemCandidate(title="Dentist", start_date=date(2026, 9, 11), start_time=time(15, 0))
        record = build_calendar_item_record(candidate, agent_name="Alfred")
        memory.remember(record)

        current = current_item_for_key(record.context["item_key"], recall=memory.recall)
        assert current is not None
        assert current.title == "Dentist"
        assert current.status == STATUS_SCHEDULED

    def test_rescheduling_an_item_supersedes_without_deleting_history(self):
        memory = EphemeralMemory()
        candidate = CalendarItemCandidate(title="Dentist", start_date=date(2026, 9, 11), start_time=time(15, 0))
        first = build_calendar_item_record(candidate, agent_name="Alfred")
        memory.remember(first)
        item_key = first.context["item_key"]

        moved = CalendarItemCandidate(title="Dentist", start_date=date(2026, 9, 12), start_time=time(16, 0))
        second = build_calendar_item_record(moved, agent_name="Alfred", item_key=item_key)
        memory.remember(second)

        current = current_item_for_key(item_key, recall=memory.recall)
        assert current is not None
        assert current.start_date == date(2026, 9, 12)
        assert current.start_time == time(16, 0)

        history = memory.recall(kind="calendar_item", tags={f"calendar_item_key:{item_key}"}, limit=10)
        assert len(history) == 2

    def test_cancelling_an_item_is_the_new_current_state(self):
        memory = EphemeralMemory()
        candidate = CalendarItemCandidate(title="Dentist", start_date=date(2026, 9, 11), start_time=time(15, 0))
        first = build_calendar_item_record(candidate, agent_name="Alfred")
        memory.remember(first)
        item_key = first.context["item_key"]

        cancelled = CalendarItemCandidate(
            title="Dentist", start_date=date(2026, 9, 11), start_time=time(15, 0), status=STATUS_CANCELLED,
        )
        memory.remember(build_calendar_item_record(cancelled, agent_name="Alfred", item_key=item_key))

        current = current_item_for_key(item_key, recall=memory.recall)
        assert current is not None
        assert current.status == STATUS_CANCELLED

    def test_a_taught_series_supersedes_the_prior_series_for_the_same_topic(self):
        memory = EphemeralMemory()
        topic_key = "it_schedule"
        series_a = "series-a"
        for day_title, start, end in (("IT Mon", time(9), time(15)),):
            candidate = CalendarItemCandidate(
                title="IT", start_time=start, end_time=end,
                recurrence=Recurrence(frequency="weekly", days_of_week=(0, 1, 2, 3, 4)),
            )
            memory.remember(build_calendar_item_record(
                candidate, agent_name="Alfred", topic_key=topic_key, series_key=series_a,
            ))

        first_series = current_series_for_topic(topic_key, recall=memory.recall)
        assert len(first_series) == 1
        assert first_series[0].start_time == time(9, 0)

        series_b = "series-b"
        replacement = CalendarItemCandidate(
            title="IT", start_time=time(10), end_time=time(16),
            recurrence=Recurrence(frequency="weekly", days_of_week=(1, 2, 3)),
        )
        memory.remember(build_calendar_item_record(
            replacement, agent_name="Alfred", topic_key=topic_key, series_key=series_b,
        ))

        current_series = current_series_for_topic(topic_key, recall=memory.recall)
        assert len(current_series) == 1
        assert current_series[0].start_time == time(10, 0)
        assert current_series[0].recurrence.days_of_week == (1, 2, 3)

        # History preserved: the old series' own record still exists.
        old_records = memory.recall(kind="calendar_item", tags={f"calendar_series:{series_a}"}, limit=10)
        assert len(old_records) == 1

    def test_all_current_calendar_items_returns_one_series_per_topic(self):
        memory = EphemeralMemory()
        memory.remember(build_calendar_item_record(
            CalendarItemCandidate(title="Dentist", start_date=date(2026, 9, 11), start_time=time(15, 0)),
            agent_name="Alfred",
        ))
        memory.remember(build_calendar_item_record(
            CalendarItemCandidate(
                title="IT", start_time=time(9), end_time=time(15),
                recurrence=Recurrence(frequency="weekly", days_of_week=(0, 1, 2, 3, 4)),
            ),
            agent_name="Alfred", topic_key="it_schedule",
        ))
        current = all_current_calendar_items(recall=memory.recall)
        titles = {c.title for c in current}
        assert titles == {"Dentist", "IT"}

    def test_no_recall_at_all_degrades_honestly(self):
        assert current_item_for_key("x", recall=None) is None
        assert current_series_for_topic("x", recall=None) == ()
        assert all_current_calendar_items(recall=None) == ()


class TestNextOccurrences:
    def test_a_one_off_item_in_the_future(self):
        candidate = CalendarItemCandidate(title="Dentist", start_date=date(2026, 9, 11), start_time=time(15, 0))
        record = build_calendar_item_record(candidate, agent_name="Alfred")
        memory = EphemeralMemory()
        memory.remember(record)
        current = current_item_for_key(record.context["item_key"], recall=memory.recall)
        assert next_occurrences(current, today=TODAY) == (date(2026, 9, 11),)

    def test_a_one_off_item_already_past_has_no_occurrences(self):
        candidate = CalendarItemCandidate(title="Old thing", start_date=date(2020, 1, 1), start_time=time(9, 0))
        record = build_calendar_item_record(candidate, agent_name="Alfred")
        memory = EphemeralMemory()
        memory.remember(record)
        current = current_item_for_key(record.context["item_key"], recall=memory.recall)
        assert next_occurrences(current, today=TODAY) == ()

    def test_a_recurring_item_expands_to_real_future_dates(self):
        candidate = CalendarItemCandidate(
            title="IT", start_time=time(9), end_time=time(15),
            recurrence=Recurrence(frequency="weekly", days_of_week=(0, 1, 2, 3, 4)),
        )
        record = build_calendar_item_record(candidate, agent_name="Alfred")
        memory = EphemeralMemory()
        memory.remember(record)
        current = current_item_for_key(record.context["item_key"], recall=memory.recall)
        occurrences = next_occurrences(current, today=TODAY, count=3)
        assert len(occurrences) == 3
        assert all(d.weekday() in (0, 1, 2, 3, 4) for d in occurrences)
        assert all(d >= TODAY for d in occurrences)

    def test_recurrence_until_bounds_the_expansion(self):
        candidate = CalendarItemCandidate(
            title="Short course", start_time=time(9),
            recurrence=Recurrence(frequency="weekly", days_of_week=(TODAY.weekday(),), until=TODAY),
        )
        record = build_calendar_item_record(candidate, agent_name="Alfred")
        memory = EphemeralMemory()
        memory.remember(record)
        current = current_item_for_key(record.context["item_key"], recall=memory.recall)
        occurrences = next_occurrences(current, today=TODAY, count=5)
        assert occurrences == (TODAY,)


class TestFindConflicts:
    def test_overlapping_recurring_items_on_shared_weekdays_conflict(self):
        existing = CalendarItemCandidate(
            title="IT", start_time=time(9), end_time=time(15),
            recurrence=Recurrence(frequency="weekly", days_of_week=(0, 1, 2, 3, 4)),
        )
        record = build_calendar_item_record(existing, agent_name="Alfred")
        memory = EphemeralMemory()
        memory.remember(record)
        current = all_current_calendar_items(recall=memory.recall)

        new_candidate = CalendarItemCandidate(
            title="Dentist", start_time=time(10), end_time=time(11),
            recurrence=Recurrence(frequency="weekly", days_of_week=(0,)),
        )
        conflicts = find_conflicts(new_candidate, current, today=TODAY)
        assert len(conflicts) == 1
        assert conflicts[0].title == "IT"

    def test_non_overlapping_times_never_conflict(self):
        existing = CalendarItemCandidate(
            title="IT", start_time=time(9), end_time=time(11),
            recurrence=Recurrence(frequency="weekly", days_of_week=(0,)),
        )
        record = build_calendar_item_record(existing, agent_name="Alfred")
        memory = EphemeralMemory()
        memory.remember(record)
        current = all_current_calendar_items(recall=memory.recall)

        new_candidate = CalendarItemCandidate(
            title="Gym", start_time=time(12), end_time=time(13),
            recurrence=Recurrence(frequency="weekly", days_of_week=(0,)),
        )
        assert find_conflicts(new_candidate, current, today=TODAY) == ()

    def test_a_candidate_with_no_time_never_falsely_claims_a_conflict(self):
        new_candidate = CalendarItemCandidate(title="Something", start_date=date(2026, 9, 11))
        assert find_conflicts(new_candidate, (), today=TODAY) == ()
