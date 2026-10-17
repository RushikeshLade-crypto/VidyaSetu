"""Indian languages offered in the UI. `speech` is the BCP-47 tag used by the
browser's speech synthesis / recognition. Add more rows to support more languages."""

LANGUAGES = {
    "hi": {"name": "Hindi", "native": "हिन्दी", "speech": "hi-IN"},
    "mr": {"name": "Marathi", "native": "मराठी", "speech": "mr-IN"},
    "bn": {"name": "Bengali", "native": "বাংলা", "speech": "bn-IN"},
    "ta": {"name": "Tamil", "native": "தமிழ்", "speech": "ta-IN"},
    "te": {"name": "Telugu", "native": "తెలుగు", "speech": "te-IN"},
    "gu": {"name": "Gujarati", "native": "ગુજરાતી", "speech": "gu-IN"},
    "kn": {"name": "Kannada", "native": "ಕನ್ನಡ", "speech": "kn-IN"},
    "ml": {"name": "Malayalam", "native": "മലയാളം", "speech": "ml-IN"},
    "pa": {"name": "Punjabi", "native": "ਪੰਜਾਬੀ", "speech": "pa-IN"},
    "ur": {"name": "Urdu", "native": "اردو", "speech": "ur-IN"},
}


# Languages Sarvam's Bulbul text-to-speech supports (of the ones we offer)
NEURAL_TTS = {"hi", "mr", "bn", "ta", "te", "gu", "kn", "ml", "pa"}


def name(code: str) -> str:
    return LANGUAGES[code]["name"]


def valid(code: str) -> bool:
    return code in LANGUAGES
