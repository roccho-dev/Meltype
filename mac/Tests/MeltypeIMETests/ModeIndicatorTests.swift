// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 HarukiKubota

import Cocoa
import XCTest
@testable import MeltypeIME

final class ModeIndicatorTests: XCTestCase {
    func testSavedSelectionIsRestoredInNewSession() {
        let suite = "ModeSelectionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let selection = ModeSelection(defaults: defaults)
        XCTAssertEqual(selection.current, .automatic)
        for mode in MacInputMode.allCases {
            selection.select(mode)
            XCTAssertEqual(ModeSelection(defaults: defaults).current, mode)
        }
    }

    func testCycleStartsAfterSavedSelectionAndUsesMenuOrder() {
        XCTAssertEqual(MacInputMode.allCases, [.automatic, .direct, .code])
        XCTAssertEqual(ModeShortcutAction.allCases.prefix(3).map { $0.mode(after: .automatic) }, MacInputMode.allCases)
        var selected = MacInputMode.automatic
        selected = ModeShortcutAction.cycle.mode(after: selected)
        XCTAssertEqual(selected, .direct)
        selected = ModeShortcutAction.cycle.mode(after: selected)
        XCTAssertEqual(selected, .code)
        selected = ModeShortcutAction.cycle.mode(after: selected)
        XCTAssertEqual(selected, .automatic)
    }

    func testCaretWindowIsBelowCursorAndClampedToDisplay() {
        let visible = NSRect(x: -1200, y: 0, width: 1200, height: 800)
        let caret = NSRect(x: -500, y: 500, width: 1, height: 20)
        let frame = ModeIndicator.frame(caret: caret, visible: visible)
        XCTAssertEqual(frame.size, ModeIndicator.content(mode: .automatic, compact: true).frame.size)
        XCTAssertLessThan(frame.width, 250)
        XCTAssertLessThan(frame.height, 44)
        XCTAssertEqual(frame.minY, caret.minY - frame.height - 6)
        XCTAssertEqual(frame.midX, caret.minX)
        let edge = ModeIndicator.frame(caret: NSRect(x: -5, y: 2, width: 1, height: 20), visible: visible)
        XCTAssertTrue(visible.contains(edge))
    }

    func testCentralWindowIsCenteredAndInvalidCaretIsRejected() {
        let visible = NSRect(x: 200, y: 100, width: 1200, height: 800)
        let frame = ModeIndicator.frame(caret: nil, visible: visible)
        XCTAssertEqual(frame.midX, visible.midX)
        XCTAssertEqual(frame.midY, visible.midY)
        XCTAssertFalse(ModeIndicator.validCaret(.zero))
        XCTAssertFalse(ModeIndicator.validCaret(NSRect(x: CGFloat.infinity, y: 0, width: 1, height: 20)))
        XCTAssertTrue(ModeIndicator.validCaret(NSRect(x: 200, y: 300, width: 0, height: 20)))
    }

    func testBothWindowLayoutsShowMenuOrderAndOnlyOneSelectedMode() {
        for compact in [false, true] {
            for mode in MacInputMode.allCases {
                let view = ModeIndicator.content(mode: mode, compact: compact)
                let labels = view.subviews.flatMap(\.subviews).compactMap { $0 as? NSTextField }
                XCTAssertEqual(labels.map(\.stringValue), ["自動", "英数", "Code"])
                let rows = view.subviews.filter { !$0.subviews.isEmpty }
                let selected = rows.filter { $0.layer?.backgroundColor != nil }
                XCTAssertEqual(selected.flatMap(\.subviews).compactMap { ($0 as? NSTextField)?.stringValue }, [mode.shortTitle])
                XCTAssertEqual(Set(rows.map { compact ? $0.frame.minY : $0.frame.midX }).count, 1)
            }
        }
    }
}
