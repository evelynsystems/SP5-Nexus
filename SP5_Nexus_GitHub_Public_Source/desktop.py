#!/usr/bin/env python3
"""SP5 Nexus standalone Qt WebEngine desktop shell.
The same local application controller runs inside this process; no browser opens.
"""
from __future__ import annotations
import os, sys, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QDesktopServices
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PySide6.QtWebEngineWidgets import QWebEngineView
import server as S

ROOT = Path(__file__).resolve().parent

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
        self.setCentralWidget(self.view)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.loadFinished.connect(self.on_loaded)
        self.view.page().linkHovered.connect(lambda url: None)
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

    def on_loaded(self, ok):
        self._loaded = ok
        if not ok:
            QMessageBox.warning(self, 'SP5 Nexus', 'The interface did not load. Check the local server and restart SP5 Nexus.')

    def toggle_full_screen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def closeEvent(self, event):
        if S.STATE['busy']:
            choice = QMessageBox.question(self,'SP5 Nexus','An operation is running. Stop it and exit?')
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            S.action('stop',{})
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
    server = ThreadingHTTPServer(('127.0.0.1', 0), S.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    S.event('SP5 Nexus native desktop window started. Powered by AnyPS5.')
    window = Desktop(server)
    window.show()
    try: return app.exec()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

if __name__ == '__main__':
    sys.exit(main())
