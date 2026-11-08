#!/usr/bin/env python3
"""SP5 Nexus standalone Qt WebEngine desktop shell.
The same local application controller runs inside this process; no browser opens.
"""
from __future__ import annotations
import os, sys, threading, time
STARTED = time.perf_counter()
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = ('server.py', 'backend.py', 'engine_manager.py', 'integrations.py', 'library.py', 'nexus_native.py',
            'updater.py', 'artwork.py', 'controllers.py', 'hostcheck.py', 'web/index.html', 'web/app.js', 'web/style.css', 'web/assets/hero_art.jpg', 'nexus.png')


def self_test():
    """`desktop.py --self-test`: check everything needed to start, without opening a window.
    Used by run.sh users, the AppImage build and support requests. Exit code 0 = ready."""
    ok = True
    def line(good, label, detail=''):
        nonlocal ok
        ok &= bool(good)
        print(f"{'PASS' if good else 'FAIL'}  {label}{': ' + detail if detail else ''}")
    line(sys.version_info >= (3, 10), 'Python', sys.version.split()[0] + ' at ' + sys.executable)
    missing = [f for f in REQUIRED if not (ROOT / f).is_file()]
    line(not missing, 'Application files', 'all present in ' + str(ROOT) if not missing else 'missing ' + ', '.join(missing))
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        line(True, 'Qt WebEngine', 'PySide6 ' + pyside_version)
    except Exception as exc:
        line(False, 'Qt WebEngine', str(exc))
    import nexus_native as N
    s = N.init()
    print(f"{'PASS' if s['state'] == N.ACTIVE else 'INFO'}  Native backend: {s['state']} {s['version']} {s['binding']} - {s['detail']}")
    import backend as B
    try:
        B.APP_DIR.mkdir(parents=True, exist_ok=True)
        line(os.access(B.APP_DIR, os.W_OK), 'Configuration folder', str(B.APP_DIR))
    except OSError as exc:
        line(False, 'Configuration folder', str(exc))
    print(f"INFO  Engine folder: {B.ENGINE_ROOT}")
    return 0 if ok else 1


if __name__ == '__main__' and '--self-test' in sys.argv:
    sys.exit(self_test())

try:
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import QAction, QIcon, QKeySequence, QDesktopServices
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage
except ImportError as exc:  # Report a readable startup error instead of a traceback.
    print(f'SP5 Nexus cannot start: Qt WebEngine (PySide6) is not available: {exc}\n'
          'Run ./run.sh, which installs the dependencies into .venv.', file=sys.stderr)
    sys.exit(2)

import json
from PySide6.QtCore import QObject, Signal
import controllers as C
import server as S


class _PadSignal(QObject):
    action = Signal(str)


def _is_local(url, port):
    return url.scheme() in ('http', '') and url.host() == '127.0.0.1' and url.port() == port


class NexusPage(QWebEnginePage):
    """Keeps the dashboard inside the window and opens outside links (GitHub, docs) in the
    user's default browser. Previously such links did nothing in the desktop window."""

    def __init__(self, port, parent=None, on_command=None):
        super().__init__(parent)
        self.port = port
        self.on_command = on_command

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == 'nexus':
            # Window commands from the interface (Control Center): nexus://fullscreen etc.
            if self.on_command:
                self.on_command(url.host())
            return False
        if url.scheme() in ('about', 'data') or _is_local(url, self.port):
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)
        if url.scheme() in ('http', 'https'):
            QDesktopServices.openUrl(url)
        return False

    def createWindow(self, _type):
        # target="_blank" links: hand the URL to a throwaway page that opens it externally.
        page = _ExternalOpener(self.port, self)
        return page


class _ExternalOpener(NexusPage):
    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() in ('http', 'https'):
            QDesktopServices.openUrl(url)
        self.deleteLater()
        return False


class Desktop(QMainWindow):
    def __init__(self, httpd):
        super().__init__()
        self.httpd = httpd
        self.setWindowTitle('SP5 Nexus | Powered by AnyPS5')
        self.resize(1600, 925)
        self.setMinimumSize(1024, 670)
        self.setStyleSheet('QMainWindow { background: #071426; }')
        ico = ROOT / 'nexus.png'
        if ico.is_file(): self.setWindowIcon(QIcon(str(ico)))
        self.view = QWebEngineView(self)
        self.view.setPage(NexusPage(httpd.server_port, self.view, on_command=self.window_command))
        # Native controller input (evdev): read on a background thread, delivered to the page
        # on the Qt main thread through a queued signal.
        self.pad_signal = _PadSignal()
        self.pad_signal.action.connect(self.on_pad_action)
        S.PAD = C.ControllerService(on_action=self.pad_signal.action.emit, settings=S.settings,
                                    paused=lambda: bool(S.STATE.get('active_game')) and not self.isActiveWindow())
        S.STATE['pad_bridge'] = True
        self.setCentralWidget(self.view)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.loadFinished.connect(self.on_loaded)
        self.view.setUrl(QUrl(f'http://127.0.0.1:{httpd.server_port}/?token={S.TOKEN}'))
        full = QAction('Toggle Full Screen', self)
        full.setShortcut(QKeySequence('F11'))
        full.triggered.connect(self.toggle_full_screen)
        self.addAction(full)
        refresh = QAction('Refresh', self)
        refresh.setShortcut(QKeySequence('Ctrl+R'))
        refresh.triggered.connect(self.view.reload)
        self.addAction(refresh)
        self._loaded = False
        self._closing = False

    def window_command(self, command):
        if command == 'fullscreen':
            self.toggle_full_screen()
        elif command == 'exit-fullscreen' and self.isFullScreen():
            self.showNormal()

    def on_pad_action(self, action):
        if not self._closing:
            self.view.page().runJavaScript(f'window.nexusInput && window.nexusInput({json.dumps(action)})')

    def on_loaded(self, ok):
        if ok and not self._closing and S.PAD is not None:
            S.PAD.start()
        if self._loaded or self._closing:
            return
        self._loaded = ok
        if ok:
            S.event(f'Interface ready in {time.perf_counter() - STARTED:.2f} s')
        else:
            QMessageBox.warning(self, 'SP5 Nexus', 'The interface did not load. Check the local server and restart SP5 Nexus.')

    def toggle_full_screen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def closeEvent(self, event):
        if S.STATE['busy'] and not getattr(self, '_force_close', False):
            choice = QMessageBox.question(self,'SP5 Nexus','An operation is running. Stop it and exit?')
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._closing = True
        if S.PAD is not None:
            S.PAD.stop()
        # Stop every process this session started (games, conversions, engine builds).
        S.shutdown()
        # Do not leave a background web service running after the window closes.
        self.view.setUrl(QUrl('about:blank'))
        super().closeEvent(event)
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()


def main():
    os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '0')
    app = QApplication(sys.argv)
    app.setApplicationName('SP5 Nexus')
    app.setDesktopFileName('sp5-nexus')
    app.setOrganizationName('SP5 Nexus')
    try:
        server = ThreadingHTTPServer(('127.0.0.1', 0), S.Handler)
    except OSError as exc:
        QMessageBox.critical(None, 'SP5 Nexus', f'The local interface service could not start: {exc}')
        return 1
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    S.event('SP5 Nexus native desktop window started. Powered by AnyPS5.')
    window = Desktop(server)
    window.show()
    # A logout or `kill` sends SIGTERM: close the window so owned games and builds are stopped.
    import signal
    def terminate(*_):
        window._force_close = True
        window.close()
    signal.signal(signal.SIGTERM, terminate)
    from PySide6.QtCore import QTimer
    keepalive = QTimer()  # lets Python run signal handlers while Qt's event loop is idle
    keepalive.start(500)
    keepalive.timeout.connect(lambda: None)
    try: return app.exec()
    finally:
        S.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

if __name__ == '__main__':
    sys.exit(main())
