"""
credit_packs.py
Single source of truth for the 5 fixed credit packs. Isolated here (not
duplicated in paystack.py or anywhere else) so a future price change is a
one-file edit — per the project's own architecture rule: anything used
across multiple pages/files lives in its own standalone file.
"""

# pack_id -> (price in Naira, credits). Price is converted to kobo
# (Paystack's base unit) at the point of use.
CREDIT_PACKS = {
    1: {"naira": 500, "credits": 20},
    2: {"naira": 1200, "credits": 50},
    3: {"naira": 4500, "credits": 200},
    4: {"naira": 10000, "credits": 450},
    5: {"naira": 25000, "credits": 1250},
}


def get_pack(pack_id: int) -> dict | None:
    return CREDIT_PACKS.get(pack_id)


def naira_to_kobo(naira: int) -> int:
    return naira * 100
