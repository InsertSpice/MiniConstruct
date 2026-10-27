"""Read-only verification of explicitly mapped ComfyUI input locations."""
import hashlib
import os
from pathlib import Path
import stat

from miniconstruct.send_timing import count


FALLBACK = "Local ComfyUI input directory is unavailable or invalid; using verified HTTP staging. Check the Connection's input root."


def input_root(value):
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Input root must be absolute")
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Input root must be a directory")
    # Check directory access without reading any media or creating files.
    with os.scandir(root):
        pass
    return root


def input_status(value):
    if not value:
        return {"status": "http", "message": "Verified HTTP staging (no local input directory configured)."}
    try:
        input_root(value)
        return {"status": "local", "message": "Local input root is accessible. Reuse requires exact staged-file size and SHA-256; the mapping is checked on Send."}
    except (OSError, ValueError, RuntimeError):
        return {"status": "warning", "message": FALLBACK}


def opened_path(stream):
    """Resolve the opened Windows handle, including junction/reparse redirection."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        function.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = function(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise OSError("Cannot resolve staged file handle")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)
    # On Linux, the open handle rather than a subsequently replaced pathname.
    return Path(os.readlink(f"/proc/self/fd/{stream.fileno()}"))


def verify_local(root, filename, digest, size):
    """None means unsafe/unavailable mapping; False means known missing/changed bytes.

    The caller validates receipt components with upload_filename before entry.
    Reject every child link/reparse point, even one pointing within the root.
    Verify the actual opened handle stays inside the explicit root before reading.
    """
    try:
        current_root = root.resolve(strict=True)
        if current_root != root:
            return None
        path = root
        for component in filename.split("/"):
            path = path / component
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return None
        if not stat.S_ISREG(info.st_mode):
            return None
        with path.open("rb") as stream:
            actual = opened_path(stream)
            if not actual.is_relative_to(root) or actual == root:
                return None
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                return False
            hasher, received = hashlib.sha256(), 0
            while chunk := stream.read(64 * 1024):
                count("localVerificationBytes", len(chunk))
                received += len(chunk)
                if received > size:
                    return False
                hasher.update(chunk)
            after = os.fstat(stream.fileno())
            return (received == size and hasher.hexdigest() == digest
                    and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    == (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
    except FileNotFoundError:
        return False
    except (OSError, ValueError, RuntimeError):
        return None
