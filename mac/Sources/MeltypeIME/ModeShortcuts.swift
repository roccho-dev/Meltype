// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 HarukiKubota

import Cocoa

enum MacInputMode: String, CaseIterable {
    case automatic, direct, code

    var title: String {
        switch self {
        case .automatic: return "日本語・英語の自動判定"
        case .direct: return "英数の直接入力"
        case .code: return "コード入力 (コメント・文字列は日本語)"
        }
    }

    var shortTitle: String {
        switch self {
        case .automatic: return "自動"
        case .direct: return "英数"
        case .code: return "Code"
        }
    }

    var next: MacInputMode {
        switch self {
        case .automatic: return .direct
        case .direct: return .code
        case .code: return .automatic
        }
    }
}

enum ModeShortcutAction: Int, CaseIterable {
    case automatic, direct, code, cycle

    var title: String {
        switch self {
        case .automatic: return MacInputMode.automatic.title
        case .direct: return MacInputMode.direct.title
        case .code: return MacInputMode.code.title
        case .cycle: return "次の入力モード"
        }
    }

    var serviceIdentifier: String {
        switch self {
        case .automatic: return "automatic"
        case .direct: return "direct"
        case .code: return "code"
        case .cycle: return "cycle"
        }
    }

    var serviceTitle: String { "Meltype：" + title }

    init?(serviceIdentifier: String) {
        guard let action = Self.allCases.first(where: { $0.serviceIdentifier == serviceIdentifier }) else { return nil }
        self = action
    }

    func mode(after current: MacInputMode) -> MacInputMode {
        switch self {
        case .automatic: return .automatic
        case .direct: return .direct
        case .code: return .code
        case .cycle: return current.next
        }
    }
}

/// ミニウィンドウの表示設定だけを保持する。キー設定は OS が管理する。
final class ModeIndicatorSettings {
    static let shared = ModeIndicatorSettings()
    private let defaults: UserDefaults
    private static let storageKey = "MacShowModeIndicator"

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    var isEnabled: Bool {
        get { defaults.object(forKey: Self.storageKey) == nil || defaults.bool(forKey: Self.storageKey) }
        set { defaults.set(newValue, forKey: Self.storageKey) }
    }
}

/// 入力欄や入力ソースが変わっても、最後に選んだ入力モードを保持する。
final class ModeSelection {
    static let shared = ModeSelection()
    private let defaults: UserDefaults
    private static let storageKey = "MacSelectedInputMode"
    private(set) var current: MacInputMode

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        defaults.register(defaults: [Self.storageKey: MacInputMode.automatic.rawValue])
        guard let value = defaults.string(forKey: Self.storageKey), let mode = MacInputMode(rawValue: value) else {
            preconditionFailure("MacSelectedInputMode must be a valid Meltype input mode")
        }
        current = mode
    }

    func select(_ mode: MacInputMode) {
        current = mode
        defaults.set(mode.rawValue, forKey: Self.storageKey)
    }
}
