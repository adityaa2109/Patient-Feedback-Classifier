"""
core/tool_backends.py

The ACTUAL tool logic, implemented once. Every adapter (raw, LangChain,
CrewAI) wires its framework-specific tool wrapper to these same functions.
This is what makes cross-framework equivalence testable: if two adapters
call the same backend with the same args, the tool_call trace is
byte-for-byte comparable regardless of which framework produced it.

Deterministic on purpose (no randomness, no wall-clock) so verification
runs are reproducible.
"""

from __future__ import annotations


def search_flights(origin: str, destination: str, date: str) -> dict:
    origin = origin.strip().upper()
    destination = destination.strip().upper()
    base = (sum(map(ord, origin)) + sum(map(ord, destination))) % 400 + 120
    return {
        "flights": [
            {
                "flight_number": f"{origin[:2]}{destination[:2]}{100 + (base % 50)}",
                "price_usd": float(base),
                "departure_time": "09:15",
            },
            {
                "flight_number": f"{origin[:2]}{destination[:2]}{200 + (base % 50)}",
                "price_usd": float(base + 65),
                "departure_time": "18:40",
            },
        ]
    }


def search_hotels(city: str, check_in: str, check_out: str) -> dict:
    city_key = city.strip().lower()
    base = (sum(map(ord, city_key)) % 150) + 80
    return {
        "hotels": [
            {"name": f"{city.title()} Grand Hotel", "price_per_night_usd": float(base), "rating": 4.2},
            {"name": f"{city.title()} Budget Inn", "price_per_night_usd": float(base - 40 if base > 60 else 40), "rating": 3.6},
        ]
    }


TOOL_REGISTRY = {
    "search_flights": search_flights,
    "search_hotels": search_hotels,
}
