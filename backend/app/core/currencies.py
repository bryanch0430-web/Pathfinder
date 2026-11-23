"""ISO-4217 currency validation for the turn and the settings form.

The FX widget can display whatever the provider returns; the trip's *stored*
currency is narrower: a real three-letter code, never free text. Aliases that
travellers commonly type (NTD, RMB) are normalised to their ISO code.
"""

from __future__ import annotations

CODES = {
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
    "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
    "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
    "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GIP", "GMD",
    "GNF", "GTQ", "GYD", "HKD", "HNL", "HTG", "HUF", "IDR", "ILS", "INR",
    "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR", "KMF",
    "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL",
    "LYD", "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU", "MUR",
    "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK", "NPR",
    "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG", "QAR",
    "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK", "SGD",
    "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SYP", "SZL", "THB", "TJS",
    "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX", "USD",
    "UYU", "UZS", "VES", "VND", "VUV", "WST", "XAF", "XCD", "XOF", "XPF",
    "YER", "ZAR", "ZMW", "ZWL",
}

ALIASES = {
    "NTD": "TWD",  # common traveller shorthand for Taiwan dollars
    "RMB": "CNY",
    "YEN": "JPY",
    "US$": "USD",
    "HK$": "HKD",
}

NAMES = {
    "AED": "UAE dirham", "AUD": "Australian dollar", "BRL": "Brazilian real",
    "CAD": "Canadian dollar", "CHF": "Swiss franc", "CNY": "Chinese yuan",
    "CZK": "Czech koruna", "DKK": "Danish krone", "EGP": "Egyptian pound",
    "EUR": "Euro", "GBP": "British pound", "HKD": "Hong Kong dollar",
    "HUF": "Hungarian forint", "IDR": "Indonesian rupiah", "ILS": "Israeli shekel",
    "INR": "Indian rupee", "ISK": "Icelandic krona", "JPY": "Japanese yen",
    "KRW": "South Korean won", "MXN": "Mexican peso", "MYR": "Malaysian ringgit",
    "NOK": "Norwegian krone", "NZD": "New Zealand dollar", "PHP": "Philippine peso",
    "PLN": "Polish zloty", "QAR": "Qatari riyal", "RON": "Romanian leu",
    "SAR": "Saudi riyal", "SEK": "Swedish krona", "SGD": "Singapore dollar",
    "THB": "Thai baht", "TRY": "Turkish lira", "TWD": "New Taiwan dollar",
    "USD": "US dollar", "VND": "Vietnamese dong", "ZAR": "South African rand",
}


def normalise_currency(value: object) -> str:
    """A supported ISO code, or an empty string for free text/nonsense."""
    text = str(value or "").strip().upper()
    text = ALIASES.get(text, text)
    if len(text) == 3 and text.isalpha() and text in CODES:
        return text
    return ""


def currency_name(code: str) -> str:
    return NAMES.get((code or "").upper(), "")
