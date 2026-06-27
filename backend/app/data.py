from datetime import date, datetime, time

from app.schemas import CalendarItem, Client, EmailMessage, Event, Lead, LeadStatus, Priority


CLIENTS = [
    Client(
        id=1,
        name="Anna Petrova",
        company="Northstar Logistics",
        email="anna.petrova@northstar.example",
        phone="+44 20 7946 0211",
        owner="Maya Chen",
        value=42000,
        last_contact=date(2026, 6, 19),
    ),
    Client(
        id=2,
        name="Daniel Brooks",
        company="Evergreen Retail",
        email="daniel.brooks@evergreen.example",
        phone="+44 20 7946 0324",
        owner="Owen Hart",
        value=28500,
        last_contact=date(2026, 6, 21),
    ),
    Client(
        id=3,
        name="Sofia Novak",
        company="BrightPath Studio",
        email="sofia.novak@brightpath.example",
        phone="+44 20 7946 0457",
        owner="Maya Chen",
        value=16750,
        last_contact=date(2026, 6, 24),
    ),
]

LEADS = [
    Lead(
        id=1,
        name="Marcus Lee",
        company="Orbit Analytics",
        email="marcus.lee@orbit.example",
        status=LeadStatus.NEW,
        source="Website",
        estimated_value=18000,
        created_at=date(2026, 6, 22),
    ),
    Lead(
        id=2,
        name="Elena Smirnova",
        company="Blue Harbour Foods",
        email="elena@blueharbour.example",
        status=LeadStatus.CONTACTED,
        source="Referral",
        estimated_value=31000,
        created_at=date(2026, 6, 20),
    ),
    Lead(
        id=3,
        name="Tom Wallace",
        company="CivicGrid",
        email="tom.wallace@civicgrid.example",
        status=LeadStatus.PROPOSAL,
        source="Conference",
        estimated_value=54000,
        created_at=date(2026, 6, 15),
    ),
]

EVENTS = [
    Event(
        id=1,
        title="Quarterly account review",
        type="Meeting",
        client="Northstar Logistics",
        starts_at=datetime(2026, 6, 29, 10, 30),
        location="Zoom",
        owner="Maya Chen",
    ),
    Event(
        id=2,
        title="Product demo",
        type="Demo",
        client="Orbit Analytics",
        starts_at=datetime(2026, 6, 30, 14, 0),
        location="London office",
        owner="Owen Hart",
    ),
    Event(
        id=3,
        title="Contract renewal call",
        type="Call",
        client="Evergreen Retail",
        starts_at=datetime(2026, 7, 2, 9, 15),
        location="Phone",
        owner="Maya Chen",
    ),
]

EMAILS = [
    EmailMessage(
        id=1,
        subject="Updated proposal attached",
        from_name="Tom Wallace",
        from_email="tom.wallace@civicgrid.example",
        preview="Thanks for the walkthrough. I added a few notes to the pricing section.",
        received_at=datetime(2026, 6, 26, 16, 42),
        unread=True,
        priority=Priority.HIGH,
    ),
    EmailMessage(
        id=2,
        subject="Re: Renewal timeline",
        from_name="Daniel Brooks",
        from_email="daniel.brooks@evergreen.example",
        preview="The first week of July works for our finance review.",
        received_at=datetime(2026, 6, 26, 11, 18),
        unread=False,
        priority=Priority.MEDIUM,
    ),
    EmailMessage(
        id=3,
        subject="Intro from the conference",
        from_name="Marcus Lee",
        from_email="marcus.lee@orbit.example",
        preview="Great meeting you this week. Could we schedule a quick demo?",
        received_at=datetime(2026, 6, 25, 18, 5),
        unread=True,
        priority=Priority.MEDIUM,
    ),
]

CALENDAR = [
    CalendarItem(
        id=1,
        title="Prepare renewal packet",
        date=date(2026, 6, 29),
        start_time=time(9, 0),
        end_time=time(10, 0),
        related_to="Evergreen Retail",
        notes="Confirm billing contacts and revised pricing.",
    ),
    CalendarItem(
        id=2,
        title="Northstar account review",
        date=date(2026, 6, 29),
        start_time=time(10, 30),
        end_time=time(11, 30),
        related_to="Northstar Logistics",
        notes="Review adoption dashboard and next quarter goals.",
    ),
    CalendarItem(
        id=3,
        title="Orbit Analytics demo",
        date=date(2026, 6, 30),
        start_time=time(14, 0),
        end_time=time(15, 0),
        related_to="Orbit Analytics",
        notes="Focus on reporting workflows and integrations.",
    ),
]

