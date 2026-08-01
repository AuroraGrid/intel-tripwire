# Improve robustness of TemporaryDirectory cleanup on Windows by retrying rmtree when
# PermissionError occurs due to transient file locks (antivirus, indexing, etc.).
import tempfile
import shutil
import time
import gc

_OriginalTemporaryDirectory = tempfile.TemporaryDirectory

class RobustTemporaryDirectory(_OriginalTemporaryDirectory):
    def __exit__(self, exc_type, exc, tb):
        # Try normal exit first
        try:
            return super().__exit__(exc_type, exc, tb)
        except PermissionError:
            # Retry a few times with GC and short sleeps to allow transient locks to clear
            for _ in range(5):
                gc.collect()
                time.sleep(0.1)
                try:
                    return super().__exit__(exc_type, exc, tb)
                except PermissionError:
                    continue
            # Final fallback: attempt rmtree ignoring errors
            try:
                shutil.rmtree(self.name, ignore_errors=True)
            except Exception:
                pass

# Replace tempfile.TemporaryDirectory globally
tempfile.TemporaryDirectory = RobustTemporaryDirectory
