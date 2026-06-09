def is_edit_mode(toolbar):
    if getattr(toolbar, "edit_mode", None) or getattr(  # cms pre 3.6
        toolbar, "edit_mode_active", None
    ):  # cms 3.6+
        return True
    return False


def is_edit_or_build_mode(toolbar):
    if (
        getattr(toolbar, "edit_mode", None)  # cms pre 3.6
        or getattr(toolbar, "edit_mode_active", None)  # cms 3.6+
        or getattr(toolbar, "build_mode", None)
        or getattr(toolbar, "build_mode_active", None)
    ):
        return True
    return False
