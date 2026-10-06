from datetime import date
from html import escape


def rupiah(amount: int) -> str:
    return "Rp " + f"{abs(int(amount)):,}".replace(",", ".")


def signed_rupiah(amount: int) -> str:
    return ("−" if int(amount) < 0 else "+") + rupiah(amount)


def date_id(value: str | date) -> str:
    parsed = date.fromisoformat(value[:10]) if isinstance(value, str) else value
    months = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


def safe(value: object) -> str:
    return escape(str(value), quote=True)