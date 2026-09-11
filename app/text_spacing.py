"""Pixel spacing shared by the image and video text compositors."""
from PIL import ImageFont


def advance(font: ImageFont.FreeTypeFont, char: str, letter: float = 0, word: float = 0) -> float:
    return float(font.getlength(char)) + letter + (word if char == " " else 0)


def text_width(font: ImageFont.FreeTypeFont, text: str, letter: float = 0, word: float = 0) -> float:
    return sum(advance(font, char, letter, word) for char in text) - (letter if text else 0)


def line_offset(font: ImageFont.FreeTypeFont, height: int) -> float:
    """Center the font's ascent/descent inside a CSS-style line box."""
    return (height - sum(font.getmetrics())) / 2
