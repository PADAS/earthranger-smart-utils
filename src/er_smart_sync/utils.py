import unicodedata


def unicode_to_ascii(input_string, replacement=""):
    """Convert a Unicode string to an ASCII string.

    Characters that cannot be converted directly are replaced with *replacement*.
    """
    normalized_string = unicodedata.normalize("NFKD", input_string)
    ascii_string = normalized_string.encode("ASCII", "ignore").decode("ASCII")

    result = []
    for char in normalized_string:
        if char in ascii_string:
            result.append(char)
        else:
            result.append(replacement)

    return "".join(result)
