"""Seed the demo database with entirely fictional organizations.

Run directly:  python seed.py
Idempotent: does nothing if the organizations table is already populated.

Every organization, counterparty and email address here is invented. Emails use
the .test TLD, which RFC 2606 reserves permanently, so none of them can ever
resolve to a real domain or receive mail.
"""

import os
import sys

from werkzeug.security import generate_password_hash

import db

MONTHS = [
    "2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02",
    "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
]

# (name, fiscal_year_end, email, password, invoice_prefix,
#  revenue_dollars_by_month, expenses_dollars_by_month)
#
# The three orgs differ by an order of magnitude on purpose: if their numbers
# looked alike, a tenant leak could pass a visual check and the negative
# assertions in the dashboard tests would be much weaker.
ORGS = [
    (
        "Alder & Finch Bookbinding", "03-31",
        "owner@alderfinch.test", "alder-demo-2026", "AF-",
        [41200, 46800, 78400, 91500, 38900, 40100,
         44600, 47200, 45800, 43300, 39700, 42500],
        [35900, 39400, 62100, 71800, 34200, 35600,
         38800, 40900, 39600, 38100, 35400, 37200],
    ),
    (
        "Cobalt Harbour Logistics", "12-31",
        "owner@cobaltharbour.test", "cobalt-demo-2026", "CH-",
        [612000, 489000, 731000, 902000, 410000, 528000,
         664000, 587000, 713000, 845000, 476000, 690000],
        [548000, 462000, 623000, 742000, 401000, 470000,
         571000, 519000, 604000, 688000, 438000, 578000],
    ),
    (
        "Meridian Fern Wellness", "06-30",
        "owner@meridianfern.test", "meridian-demo-2026", "MF-",
        [61400, 64200, 67900, 71100, 74800, 78200,
         82600, 86300, 90100, 95400, 101200, 108700],
        [43800, 45100, 47600, 49300, 51900, 54100,
         57200, 59400, 61800, 64900, 68300, 72400],
    ),
]

# (org_index, number_suffix, counterparty, issued_on, amount_cents, status)
# One non-round amount per org, so the cents path is exercised by real data.
INVOICES = [
    (0, "0141", "Quillmark Stationers", "2026-03-04", 812450, "paid"),
    (0, "0142", "Verdant Press Co-op", "2026-04-11", 1240000, "paid"),
    (0, "0143", "Hollowbrook Archive", "2026-05-02", 356000, "outstanding"),
    (0, "0144", "Pemberton Rare Books", "2026-06-19", 978300, "outstanding"),
    (0, "0145", "Saltmarsh Bindery Supply", "2026-07-08", 214000, "overdue"),
    (0, "0146", "Tolliver Paper Mill", "2026-08-01", 655000, "outstanding"),
    (1, "2207", "Drayfield Container Line", "2026-02-27", 18450075, "paid"),
    (1, "2208", "Northgate Freight Union", "2026-03-30", 9820000, "paid"),
    (1, "2209", "Pellowe Cold Storage", "2026-05-14", 27300000, "outstanding"),
    (1, "2210", "Kestrel Bay Terminals", "2026-06-06", 14150000, "outstanding"),
    (1, "2211", "Ashgrove Haulage Group", "2026-07-21", 6740000, "overdue"),
    (1, "2212", "Larkin Shoreline Depot", "2026-08-12", 21980000, "outstanding"),
    (1, "2213", "Windward Crate Services", "2026-08-25", 8305000, "outstanding"),
    (2, "0518", "Bramblewood Retreat", "2026-03-12", 428900, "paid"),
    (2, "0519", "Cedar Lantern Studio", "2026-04-23", 316000, "paid"),
    (2, "0520", "Halcyon Fields Clinic", "2026-05-29", 592000, "outstanding"),
    (2, "0521", "Rowan Terrace Spa", "2026-06-30", 274500, "outstanding"),
    (2, "0522", "Juniper Hollow Wellness", "2026-07-17", 461000, "overdue"),
    (2, "0523", "Marisol Grove Therapy", "2026-08-09", 733200, "outstanding"),
]

CREDENTIALS = [(org[2], org[3]) for org in ORGS]


def build(path):
    """Create the schema if absent and populate it once. True if it inserted."""
    conn = db.connect(path)
    has_schema = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'organizations'"
    ).fetchone()[0]
    if not has_schema:
        db.init(conn)
    if conn.execute("SELECT count(*) FROM organizations").fetchone()[0]:
        conn.close()
        return False

    org_ids = []
    for name, fye, email, password, _prefix, revenue, expenses in ORGS:
        cur = conn.execute(
            "INSERT INTO organizations (name, fiscal_year_end) VALUES (?, ?)",
            (name, fye),
        )
        org_id = cur.lastrowid
        org_ids.append(org_id)
        conn.execute(
            "INSERT INTO users (org_id, email, password_hash) VALUES (?, ?, ?)",
            (org_id, email, generate_password_hash(password)),
        )
        for month, rev, exp in zip(MONTHS, revenue, expenses):
            conn.execute(
                "INSERT INTO monthly_figures (org_id, month, revenue_cents, expenses_cents)"
                " VALUES (?, ?, ?, ?)",
                (org_id, month, rev * 100, exp * 100),
            )

    for idx, suffix, counterparty, issued_on, amount_cents, status in INVOICES:
        conn.execute(
            "INSERT INTO invoices (org_id, number, counterparty, issued_on, amount_cents, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (org_ids[idx], ORGS[idx][4] + suffix, counterparty, issued_on, amount_cents, status),
        )

    conn.commit()
    conn.close()
    return True


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DB_PATH", "portal.db")
    # The wording matters: this runs on every Render boot and is the first
    # thing you read in the deploy log when something looks wrong.
    if build(target):
        print(f"seeded {target}")
    else:
        print(f"{target} already populated, nothing to do")
    for email, password in CREDENTIALS:
        print(f"  {email} / {password}")
