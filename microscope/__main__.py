import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from .capture import CaptureThread
from .mainwindow import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    try:
        from .useeplus import _backend
        _backend()
    except RuntimeError as e:
        QMessageBox.critical(None, "Microscope", str(e))
        return 1
    cap = CaptureThread()
    win = MainWindow(cap)
    win.resize(1100, 760)
    win.show()
    cap.start()
    app.aboutToQuit.connect(cap.stop)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
