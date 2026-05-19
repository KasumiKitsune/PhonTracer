import customtkinter as ctk

_shared_root = None

def get_shared_root():
    global _shared_root
    if _shared_root is None:
        _shared_root = ctk.CTk()
        _shared_root.withdraw()
    return _shared_root
