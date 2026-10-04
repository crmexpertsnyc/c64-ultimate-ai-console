"""File types by what can use them."""

# What the in-browser VICE emulator (EmulatorJS vice_x64sc) can start.
EMULATOR_FORMATS = {"d64", "d71", "d81", "g64", "x64", "t64", "tap", "prg", "p00", "crt"}
# Disk images (several of them form one multi-disk bundle for the emulator).
DISK_FORMATS = {"d64", "d71", "d81", "g64", "x64"}


def browser_playable(fmt: str | None, category: str | None = None) -> bool:
    return bool(fmt) and fmt.lower() in EMULATOR_FORMATS and category != "music"
