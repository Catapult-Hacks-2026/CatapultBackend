import random
import uuid

from app.services.rag import add_negotiation_to_history

VENDORS = [
    "Acme Industrial Supply",
    "Midwest Components",
    "ForgeLine Manufacturing",
    "Blue River Packaging",
    "Summit Logistics",
    "Northstar Electronics",
]

CATEGORIES = [
    "fasteners",
    "microcontrollers",
    "packaging",
    "steel tubing",
    "freight",
    "sensors",
]

SEASONS = ["Q1 restock", "Q2 expansion", "Q3 inventory reset", "Q4 discount push"]


def seed_history(count: int = 30) -> None:
    rng = random.Random(42)
    for index in range(count):
        vendor = rng.choice(VENDORS)
        category = rng.choice(CATEGORIES)
        season = rng.choice(SEASONS)
        opening_price = round(rng.uniform(45, 180), 2)
        final_price = round(opening_price * rng.uniform(0.82, 0.98), 2)
        shipping = round(rng.uniform(0, 250), 2)
        payment_terms = rng.choice([30, 45, 60, 75])
        delivery = rng.choice([7, 10, 14, 21, 28])
        outcome = rng.choice(["accepted", "accepted", "rejected", "escalated"])
        messages = [
            {
                "role": "vendor",
                "content": f"Opening quote for {category} during {season}.",
                "structured_data": {
                    "unit_price": opening_price,
                    "shipping_cost": shipping + 25,
                    "payment_terms_days": 30,
                    "delivery_days": delivery + 3,
                    "notes": category,
                },
            },
            {
                "role": "agent",
                "content": f"Buyer pushed back citing budget and prior {category} benchmarks.",
                "structured_data": {
                    "unit_price": round((opening_price + final_price) / 2, 2),
                    "shipping_cost": shipping,
                    "payment_terms_days": max(payment_terms, 45),
                    "delivery_days": delivery,
                    "notes": f"{category} {season}",
                },
            },
            {
                "role": "vendor",
                "content": "Vendor responded with a final concession package.",
                "structured_data": {
                    "unit_price": final_price,
                    "shipping_cost": shipping,
                    "payment_terms_days": payment_terms,
                    "delivery_days": delivery,
                    "notes": f"{category} {season}",
                },
            },
        ]
        final_offer = messages[-1]["structured_data"]
        add_negotiation_to_history(
            negotiation_id=f"seed-{index}-{uuid.uuid4()}",
            vendor_name=vendor,
            messages=messages,
            final_offer=final_offer,
            outcome=outcome,
        )


if __name__ == "__main__":
    seed_history()
