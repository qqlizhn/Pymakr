# -*- coding: utf-8 -*-

# Copyright (c) 2002 - 2016 Detlev Offenbach <detlev@die-offenbachs.de>
#

"""
Module implementing a graphical Python shell.
"""

from __future__ import unicode_literals

from PyQt5.QtGui import QFont, QPalette
from PyQt5.QtCore import pyqtSignal, Qt, pyqtSlot
from PyQt5.QtWidgets import QWidget, QApplication, QHBoxLayout, \
    QVBoxLayout, QMenu, QShortcut
from PyQt5.Qsci import QsciScintilla
from PyQt5 import QtGui

from QScintilla.QsciScintillaCompat import QsciScintillaCompat
import Preferences
from E5Gui.E5Application import e5App
import UI.PixmapCache
import sys
import re
import os

class ShellAssembly(QWidget):
    """
    Class implementing the containing widget for the shell.
    """
    def __init__(self, dbs, vm, horizontal=True, parent=None):
        """
        Constructor
        
        @param dbs reference to the debug server object
        @param vm reference to the viewmanager object
        @param horizontal flag indicating a horizontal layout (boolean)
        @param parent parent widget (QWidget)
        """
        super(ShellAssembly, self).__init__(parent)

        self.__shell = UPythonShell(dbs, vm, self)
        
        from UI.SearchWidget import SearchWidget
        self.__searchWidget = SearchWidget(self.__shell, self, horizontal)
        self.__searchWidget.hide()
        
        if horizontal:
            self.__layout = QHBoxLayout(self)
        else:
            self.__layout = QVBoxLayout(self)
        self.__layout.setContentsMargins(1, 1, 1, 1)
        self.__layout.addWidget(self.__shell)
        self.__layout.addWidget(self.__searchWidget)
        
        self.__searchWidget.searchNext.connect(self.__shell.searchNext)
        self.__searchWidget.searchPrevious.connect(self.__shell.searchPrev)
        self.__shell.searchStringFound.connect(
            self.__searchWidget.searchStringFound)

    def showFind(self, txt=""):
        """
        Public method to display the search widget.
        
        @param txt text to be shown in the combo (string)
        """
        self.__searchWidget.showFind(txt)
    
    def shell(self):
        """
        Public method to get a reference to the terminal widget.
        
        @return reference to the shell widget (Shell)
        """
        return self.__shell


class UPythonShell(QsciScintillaCompat):
    """
    Class implementing a graphical Python shell.
    
    A user can enter commands that are executed in the remote
    Python interpreter.
    
    @signal searchStringFound(found) emitted to indicate the search
        result (boolean)
    """
    searchStringFound = pyqtSignal(bool)
    focusChanged = pyqtSignal(bool)
    
    def __init__(self, dbs, vm, parent=None):
        """
        Constructor
        
        @param dbs reference to the debug server object
        @param vm reference to the viewmanager object
        @param parent parent widget (QWidget)
        """
        super(UPythonShell, self).__init__(parent)
        self.__rxBuffer = None
        self.patch()
        self.setUtf8(True)
        
        self.vm = vm
        self.__mainWindow = parent
        self.__lastSearch = ()
        self.__viewManager = e5App().getObject("ViewManager")
        
        self.linesepRegExp = r"\r\n|\n|\r"
        
        self.setWindowTitle(self.tr('Pycom Device Shell'))

        # #todo improve this text (original was more complete)        
        self.setWhatsThis(self.tr(
            """<b>The Pycom Shell Window</b>"""
            """<p>This is a direct connection into a Pycom Device</p>"""
        ))

        self.linesChanged.connect(self.__resizeLinenoMargin)

        dbs.dataReceptionEvent.connect(self.__write)
        self.dbs = dbs
        
        # Initialize instance variables.
        self.__initialize()
        self.prline = 0
        self.prcol = 0
        self.inDragDrop = False
        self.lexer_ = None
        
        self.clientType = 'Python3'
        
        # clear QScintilla defined keyboard commands
        # we do our own handling through the view manager
        self.__actionsAdded = False
        
        # Create a little context menu
        self.menu = QMenu(self)
        self.menu.addAction(self.tr('Cut'), self.cut)
        self.menu.addAction(self.tr('Copy'), self.copy)
        self.menu.addAction(self.tr('Paste'), self.__paste)
        self.menu.addSeparator()
        self.menu.addAction(self.tr('Find'), self.__find)
        self.menu.addSeparator()
        self.menu.addAction(self.tr('Clear'), self.clear)
        self.menu.addAction(self.tr('Reset'), self.__reset)
        self.menu.addSeparator()
        self.menu.addAction(self.tr("Configure..."), self.__configure)

        self.__bindLexer()
        self.__setTextDisplay()
        self.__setMargin0()
        
        self.incrementalSearchString = ""
        self.incrementalSearchActive = False
        
        self.grabGesture(Qt.PinchGesture)

        self.focusChanged.connect(self.__focusChanged)
        self.dbs.statusChanged.connect(self.notifyStatus)
        self.dbs.pyboardError.connect(self.pyboardError)
        self.__printWelcome()

        

        # advanced key features
        self.ctrl_active = False
        self.cmd_active = False

    def __focusChanged(self,result):
        self.dbs.tryConnecting(result)
        self.ctrl_active = False
        self.cmd_active = False

    def __toVT100(self, key):
        key = key.key()
        if key == Qt.Key_Up:
            return b'\x1b[A'
        elif key == Qt.Key_Down:
            return b'\x1b[B'
        elif key == Qt.Key_Right:
            return b'\x1b[C'
        elif key == Qt.Key_Left:
            return b'\x1b[D'
        elif key == Qt.Key_Home:
            return b'\x01'
        elif key == Qt.Key_End:
            return b'\x05'
        return ''

    def patch(self):
        self.passive = False

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key_Control:
            self.ctrl_active = False
            
        elif ev.key() == Qt.Key_Meta:
            self.cmd_active = False

    def keyPressEvent(self, ev):
        """
        Protected method to handle the user input a key at a time.
        
        @param ev key event (QKeyEvent)
        """

        if ev.key() == Qt.Key_Enter: # make mac right "enter" key behave as "return"
            txt = '\r'
        else:
            txt = ev.text()
        if not txt:
            txt = self.__toVT100(ev)

        if txt:
            self.dbs.send(txt)
            ev.accept()
        else:
            ev.ignore

        osFamily = sys.platform.rstrip('1234567890')
        if osFamily == 'win32' or osFamily == 'win64':
            osFamily == 'win'

        if ev.key() == Qt.Key_Control:
            self.ctrl_active = True
        elif ev.key() == Qt.Key_Meta:
            self.cmd_active = True
        
        # ctrl-c ctrl-v logic.
        if (self.ctrl_active or self.cmd_active) and ev.key() == Qt.Key_V:
            self.__paste()
        elif (self.ctrl_active or self.cmd_active) and ev.key() == Qt.Key_C:
            if self.cmd_active: 
                self.__stopRunningPrograms()
            elif self.ctrl_active:
                if osFamily == 'win': # for windows, we want the ctrl to do both copy as well as reset
                    if self.hasSelectedText():
                        self.copy()
                    # else: 
                    #     self.__stopRunningPrograms() # turns out, this is not needed, because ctrl-c already works in windows! somehow....
                else:
                    self.copy()
        

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.hasFocus():
            self.focusChanged.emit(True)
        super(UPythonShell, self).mouseReleaseEvent(event)

    def focusInEvent(self, event):
        """
        Protected method called when the shell receives focus.
        
        @param event the event object (QFocusEvent)
        """
        self.setFocus()
        if not self.__actionsAdded:
            self.addActions(self.vm.copyActGrp.actions())
            self.addActions(self.vm.viewActGrp.actions())
            self.__searchShortcut = QShortcut(
                self.vm.searchAct.shortcut(), self,
                self.__find, self.__find)
            self.__searchNextShortcut = QShortcut(
                self.vm.searchNextAct.shortcut(), self,
                self.__searchNext, self.__searchNext)
            self.__searchPrevShortcut = QShortcut(
                self.vm.searchPrevAct.shortcut(), self,
                self.__searchPrev, self.__searchPrev)
        
        try:
            self.vm.editorActGrp.setEnabled(False)
            self.vm.copyActGrp.setEnabled(True)
            self.vm.viewActGrp.setEnabled(True)
            self.vm.searchActGrp.setEnabled(False)
        except AttributeError:
            pass
        self.__searchShortcut.setEnabled(True)
        self.__searchNextShortcut.setEnabled(True)
        self.__searchPrevShortcut.setEnabled(True)
        self.setCaretWidth(self.caretWidth)
        self.setCursorFlashTime(QApplication.cursorFlashTime())
        # moved the next line to the mouseRelease event
        # self.focusChanged.emit(True)
        super(UPythonShell, self).focusInEvent(event)
        
    def focusOutEvent(self, event):
        """
        Protected method called when the shell loses focus.
        
        @param event the event object (QFocusEvent)
        """
        self.__searchShortcut.setEnabled(False)
        self.__searchNextShortcut.setEnabled(False)
        self.__searchPrevShortcut.setEnabled(False)
        self.setCaretWidth(0)
        self.focusChanged.emit(False)
        super(UPythonShell, self).focusOutEvent(event)

    def refreshLexer(self):
        self.__bindLexer()
        self.__setTextDisplay()
        self.__setMargin0()

    def __bindLexer(self, language='Python3'):
        """
        Private slot to set the lexer.
        
        @param language lexer language to set (string)
        """
        self.language = language
        if Preferences.getShell("SyntaxHighlightingEnabled"):
            from QScintilla import Lexers 
            self.lexer_ = Lexers.getLexer(self.language, self)
        else:
            self.lexer_ = None
        
        if self.lexer_ is None:
            self.setLexer(None)
            font = Preferences.getShell("MonospacedFont")
            self.monospacedStyles(font)
            return
        
        # get the font for style 0 and set it as the default font
        key = 'Scintilla/{0}/style0/font'.format(self.lexer_.language())
        fdesc = Preferences.Prefs.settings.value(key)
        if fdesc is not None:
            font = QFont(fdesc[0], int(fdesc[1]))
            self.lexer_.setDefaultFont(font)
        self.setLexer(self.lexer_)
        self.lexer_.readSettings(Preferences.Prefs.settings, "Scintilla")
        
        # initialize the lexer APIs settings
        api = self.vm.getAPIsManager().getAPIs(self.language)
        if api is not None:
            api = api.getQsciAPIs()
            if api is not None:
                self.lexer_.setAPIs(api)
        
        self.lexer_.setDefaultColor(self.lexer_.color(0))
        self.lexer_.setDefaultPaper(self.lexer_.paper(0))
        

    def __setMargin0(self):
        """
        Private method to configure margin 0.
        """
        # set the settings for all margins
        self.setMarginsFont(Preferences.getShell("MarginsFont"))
        self.setMarginsForegroundColor(
            Preferences.getEditorColour("MarginsForeground"))
        self.setMarginsBackgroundColor(
            Preferences.getEditorColour("MarginsBackground"))
        
        # set margin 0 settings
        linenoMargin = Preferences.getShell("LinenoMargin")
        self.setMarginLineNumbers(0, linenoMargin)
        if linenoMargin:
            self.__resizeLinenoMargin()
        else:
            self.setMarginWidth(0, 0)
        
        # disable margins 1 and 2
        self.setMarginWidth(1, 0)
        self.setMarginWidth(2, 0)
        
    def __setTextDisplay(self):
        """
        Private method to configure the text display.
        """
        self.setTabWidth(Preferences.getEditor("TabWidth"))
        if Preferences.getEditor("ShowWhitespace"):
            self.setWhitespaceVisibility(QsciScintilla.WsVisible)
            try:
                self.setWhitespaceForegroundColor(
                    Preferences.getEditorColour("WhitespaceForeground"))
                self.setWhitespaceBackgroundColor(
                    Preferences.getEditorColour("WhitespaceBackground"))
                self.setWhitespaceSize(
                    Preferences.getEditor("WhitespaceSize"))
            except AttributeError:
                # QScintilla before 2.5 doesn't support this
                pass
        else:
            self.setWhitespaceVisibility(QsciScintilla.WsInvisible)
        self.setEolVisibility(Preferences.getEditor("ShowEOL"))
        if Preferences.getEditor("BraceHighlighting"):
            self.setBraceMatching(QsciScintilla.SloppyBraceMatch)
        else:
            self.setBraceMatching(QsciScintilla.NoBraceMatch)
        self.setMatchedBraceForegroundColor(
            Preferences.getEditorColour("MatchingBrace"))
        self.setMatchedBraceBackgroundColor(
            Preferences.getEditorColour("MatchingBraceBack"))
        self.setUnmatchedBraceForegroundColor(
            Preferences.getEditorColour("NonmatchingBrace"))
        self.setUnmatchedBraceBackgroundColor(
            Preferences.getEditorColour("NonmatchingBraceBack"))
        if Preferences.getEditor("CustomSelectionColours"):
            self.setSelectionBackgroundColor(
                Preferences.getEditorColour("SelectionBackground"))
        else:
            self.setSelectionBackgroundColor(
                QApplication.palette().color(QPalette.Highlight))
        if Preferences.getEditor("ColourizeSelText"):
            self.resetSelectionForegroundColor()
        elif Preferences.getEditor("CustomSelectionColours"):
            self.setSelectionForegroundColor(
                Preferences.getEditorColour("SelectionForeground"))
        else:
            self.setSelectionForegroundColor(
                QApplication.palette().color(QPalette.HighlightedText))
        self.setSelectionToEol(Preferences.getEditor("ExtendSelectionToEol"))
        self.setCaretForegroundColor(
            Preferences.getEditorColour("CaretForeground"))
        self.setCaretLineVisible(False)
        self.caretWidth = Preferences.getEditor("CaretWidth")
        self.setCaretWidth(self.caretWidth)
        if Preferences.getShell("WrapEnabled"):
            self.setWrapMode(QsciScintilla.WrapWord)
        else:
            self.setWrapMode(QsciScintilla.WrapNone)
        self.useMonospaced = Preferences.getShell("UseMonospacedFont")
        self.__setMonospaced(self.useMonospaced)
        
        self.setCursorFlashTime(QApplication.cursorFlashTime())
        
        if Preferences.getEditor("OverrideEditAreaColours"):
            self.setColor(Preferences.getEditorColour("EditAreaForeground"))
            self.setPaper(Preferences.getEditorColour("EditAreaBackground"))
        
    def __setMonospaced(self, on):
        """
        Private method to set/reset a monospaced font.
        
        @param on flag to indicate usage of a monospace font (boolean)
        """
        if on:
            if not self.lexer_:
                f = Preferences.getShell("MonospacedFont")
                self.monospacedStyles(f)
        else:
            if not self.lexer_:
                self.clearStyles()
                self.__setMargin0()
            self.setFont(Preferences.getShell("MonospacedFont"))
        
        self.useMonospaced = on

    def __resizeLinenoMargin(self):
        """
        Private slot to resize the line numbers margin.
        """
        linenoMargin = Preferences.getShell("LinenoMargin")
        if linenoMargin:
            self.setMarginWidth(0, '8' * (len(str(self.lines())) + 1))

    def __isCursorOnLastLine(self):
        """
        Private method to check, if the cursor is on the last line.
        
        @return flag indicating that the cursor is on the last line (boolean)
        """
        cline, ccol = self.getCursorPosition()
        return cline == self.lines() - 1

    def __getEndPos(self):
        """
        Private method to return the line and column of the last character.
        
        @return tuple of two values (int, int) giving the line and column
        """
        line = self.lines() - 1
        return (line, len(self.text(line)))

    def __find(self):
        """
        Private slot to show the find widget.
        """
        txt = self.selectedText()
        self.__mainWindow.showFind(txt)

    def __searchNext(self):
        """
        Private method to search for the next occurrence.
        """
        if self.__lastSearch:
            self.searchNext(*self.__lastSearch)
    
    def searchNext(self, txt, caseSensitive, wholeWord):
        """
        Public method to search the next occurrence of the given text.
        
        @param txt text to search for (string)
        @param caseSensitive flag indicating to perform a case sensitive
            search (boolean)
        @param wholeWord flag indicating to search for whole words
            only (boolean)
        """
        self.__lastSearch = (txt, caseSensitive, wholeWord)
        ok = self.findFirst(
            txt, False, caseSensitive, wholeWord, False, forward=True)
        self.searchStringFound.emit(ok)

    def __searchPrev(self):
        """
        Private method to search for the next occurrence.
        """
        if self.__lastSearch:
            self.searchPrev(*self.__lastSearch)
    
    def searchPrev(self, txt, caseSensitive, wholeWord):
        """
        Public method to search the previous occurrence of the given text.
        
        @param txt text to search for (string)
        @param caseSensitive flag indicating to perform a case sensitive
            search (boolean)
        @param wholeWord flag indicating to search for whole words
            only (boolean)
        """
        self.__lastSearch = (txt, caseSensitive, wholeWord)
        if self.hasSelectedText():
            line, index = self.getSelection()[:2]
        else:
            line, index = -1, -1
        ok = self.findFirst(
            txt, False, caseSensitive, wholeWord, False,
            forward=False, line=line, index=index)
        self.searchStringFound.emit(ok)

    def contextMenuEvent(self, ev):
        """
        Protected method to show our own context menu.
        
        @param ev context menu event (QContextMenuEvent)
        """
        self.menu.popup(ev.globalPos())
        ev.accept()

    def __reset(self):
        """
        Private slot to handle the 'reset' context menu entry.
        """
        self.dbs.restart()

    def __stopRunningPrograms(self):
        """
        Private slot to handle a ctrl c in the console
        """
        self.dbs.send(b'\x03')
        # self.dbs.stopRunningPrograms()

    def __initialize(self):
        """
        Private method to get ready for a new remote interpreter.
        """
        self.buff = ""
        self.inContinue = False
        self.echoInput = True
        self.clientCapabilities = 0
        self.inCommandExecution = False
        self.interruptCommandExecution = False

    def __moveCursorRel(self, relPos):
            line, col = self.getCursorPosition()
            col += relPos
            self.setCursorPosition(line, col)

    def __overwrite(self, text):
        line, col = self.getCursorPosition()
        self.setSelection(line, col, line, col + len(text))
        text = text.replace('\x04', '')
        self.replaceSelectedText(text)

    def __insertNewLine(self):
        line, col = self.__getEndPos()
        self.setCursorPosition(line, col)
        self.__overwrite("\n")

    def __simulateVT100(self, s):
        if s[0] == b'\x08':
            self.moveCursorLeft()
            self.__movement = True
            return 1
        elif s[0] == b'\r':
            return 1
        elif s[0] == b'\n':
            self.__insertNewLine()
            self._movement = True
            return 1
        elif s[0:2] == b'\x1b[':
            s = s[2:]

            # when the command itself is not in this message, add back to the buffer
            if len(s) == 0:
                return 0

            # erase from cursor to end of line	
            if s[0] == 'K':
                self.deleteLineRight()
                return 3

            # Cursor Backward
            if s.find('D') != -1:
                pos = s.find('D')
                numChars = int(s[:pos])
                self.__moveCursorRel(-numChars)
                self.__movement = True
                return 3 + pos
            
            # when there is data but no D or K, add it back to the buffer
            return 0
        elif s[0] == b'\x1b':
            return 0
        return -1

    def __write(self, s):
        """
        Private method to display some text.
        
        @param s text to be displayed (string)
        """
        try:
            self.setCursorPosition(self.keyLine, self.keyCol)
        except:
            pass

        i = 0
        if self.__rxBuffer:
            s = self.__rxBuffer + s
            self.__rxBuffer = None
        while i < len(s):
            self.__movement = False
            advance = self.__simulateVT100(s[i:])
            if advance == -1:
                self.__overwrite(s[i])
                i += 1
            elif advance == 0:
                self.__rxBuffer = s[i:]
                break
            else:
                i += advance

        self.keyLine, self.keyCol = self.getCursorPosition()
        self.ensureCursorVisible()
        self.ensureLineVisible(self.keyLine)

    def __insertTextNoEcho(self, s):
        """
        Private method to insert some text at the end of the buffer without
        echoing it.
        
        @param s text to be inserted (string)
        """
        self.buff += s
        self.prline, self.prcol = self.getCursorPosition()

    def __paste(self):
        text = QApplication.clipboard().text()
        text = re.sub(re.compile('\r|\n|\r\n', re.MULTILINE),'\n', text)
        text = re.sub(re.compile('^\s*', re.MULTILINE), '', text)
        text = text.replace('\n', '\r')
        self.dbs.send(text)

    def __clientStatement(self, more):
        """
        Private method to handle the response from the debugger client.
        
        @param more flag indicating that more user input is required (boolean)
        """
        self.inContinue = more
        self.__writePrompt()
        self.inCommandExecution = False

    def __writePrompt(self):
        """
        Private method to write the prompt.
        """
        self.__write('')

    def __configure(self):
        """
        Private method to open the configuration dialog.
        """
        e5App().getObject("UserInterface").showPreferences("MicroPython")

    def __printWelcome(self):
        if not self.dbs.isConfigured():
            if not hasattr(QtGui, "qt_mac_set_native_menubar"):
                notConfiguredMsg = "Settings"
            else:
                notConfiguredMsg = "Pymakr"
            notConfiguredMsg = "Proceed to configure a Pycom device in: {0} > Preferences > Pycom Device\n".format(notConfiguredMsg)
            self.__write(self.tr(notConfiguredMsg))
        else:
            self.notifyStatus("connecting")

    @pyqtSlot(str)
    def pyboardError(self,error):
        if self.dbs.uname:
            dev_str = self.dbs.uname[0]
        else:
            dev_str = "Pycom device"

        self.__write(self.tr("> {0}. (click to attempt to reconnect)\n".format(error)))

    @pyqtSlot(str)
    def notifyStatus(self, status):
        if self.dbs.uname:
            dev_str = self.dbs.uname[0]
        else:
            dev_str = "Pycom device"


        if status == "connecting":
            self.__write(self.tr("Connecting to a {0}...\n".format(dev_str)))
        if status == "connected":
            self.__write(self.tr("Connected\n"))
        elif status == "disconnected":
            self.__write(self.tr("Connection closed\n"))
        elif status == "lostconnection":
            self.__write(self.tr("Lost connection with the {0}! (click to attempt to reconnect)\n".format(dev_str)))
        elif status == "softreset":
            self.__write(self.tr("Soft resetting the {0}\n".format(dev_str)))
        elif status == "error":
            self.__write(self.tr("Error while communicating with the {0}! (click to attempt to reconnect)\n".format(dev_str)))
        elif status == "reattempt":
            self.__write(self.tr("Reattempting in a few seconds...\n"))
        elif status == "invcredentials":
            self.__write(self.tr("Invalid credentials, please check device username and password\n"))
        elif status == "invaddress":
            self.__write(self.tr("Invalid device address, please check the settings\n"))
        elif status == "runinit":
            editor = self.__viewManager.activeWindow()
            code = editor.text()
            fn = editor.getFileName()
            txt = os.path.basename(fn)
            self.__write(self.tr("\n\nRunning {0}\n".format(txt)))
        elif status == "syncinit":
            self.__write(self.tr("\n\nSyncing the project with the {0}. Please wait...\n".format(dev_str)))
        elif status == "syncend":
            self.__write(self.tr("Successfully synced!\n\n"))
        elif status == "syncfailed":
            self.__write(self.tr("Syncing failed!\n\n"))
        elif status == "syncfailed_connection":
            self.__write(self.tr("Sync failed, connection to the {0} was lost\n\n".format(dev_str)))
        elif status == "syncfailed_busy":
            self.__write(self.tr("Sync is already in progress\n\n"))
        elif status == "syncfailed_project":
            self.__write(self.tr("Syncing failed, there is no project selected\n\n"))