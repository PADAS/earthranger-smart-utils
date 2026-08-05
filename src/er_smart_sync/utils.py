import unicodedata

# smartconnect's resolve_display() substitutes this literal whenever no
# <names>/<name> entry matches the requested language code.
UNRESOLVED_DISPLAY = "n/a"


def _iter_displays(model_dict):
    """Yield every resolved display string in an exported SMART model dict."""
    for category in model_dict.get("categories") or []:
        yield category.get("display")
    for attribute in model_dict.get("attributes") or []:
        yield attribute.get("display")
        for option in attribute.get("options") or []:
            yield option.get("display")


def _available_language_codes(model):
    """Collect the language codes declared in a parsed SMART model.

    smartconnect exposes the underlying untangle tree only as an
    implementation detail, so this walks it defensively: an unexpected
    shape yields an empty set rather than raising.
    """
    root = getattr(model, "datamodel", None)
    if root is None:
        root = getattr(model, "config_datamodel", None)
    codes = set()
    if root is None:
        return codes

    stack = [root]
    while stack:
        node = stack.pop()
        attributes = getattr(node, "_attributes", None)
        if isinstance(attributes, dict):
            # <languages code="es"/> (data model) and <language code="es"/>
            # (configurable model) declare the set of translations up front;
            # every <names language_code="es"/> carries one directly.
            code = attributes.get("language_code")
            if not code and getattr(node, "_name", "").startswith("language"):
                code = attributes.get("code")
            if code:
                codes.add(code)
        stack.extend(getattr(node, "children", None) or [])
    return codes


def describe_unresolved_language(language_code, models):
    """Return a warning message if SMART labels failed to resolve, else None.

    An unmatched language code is not an error in smartconnect — every label
    silently becomes the literal "n/a", and a full sync will happily push
    those to EarthRanger as if they were real display names (GH #13).
    """
    models = [m for m in models if m is not None]
    displays = [
        display
        for model in models
        for display in _iter_displays(model.export_as_dict())
        if display
    ]
    if not displays or any(d != UNRESOLVED_DISPLAY for d in displays):
        return None

    available = sorted(
        {
            code
            for model in models
            for code in _available_language_codes(model)
            if code != language_code
        }
    )
    message = (
        f"All {len(displays)} SMART display names resolved to "
        f'"{UNRESOLVED_DISPLAY}": the data model has no labels in language '
        f"{language_code!r}."
    )
    if available:
        message += (
            f" Available language code(s): {', '.join(available)}. "
            f"Set smart.use_language_code in your config (or pass "
            f"--smart-language) to one of these and re-run."
        )
    return message


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
