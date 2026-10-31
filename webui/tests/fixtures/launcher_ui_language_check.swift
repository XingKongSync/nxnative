import Foundation

@main struct UILanguageCheck {
    static func main() throws {
        let suite = "launcher-ui-check-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let data = Data(#"{"settings":[{"env":"DM_LANG","kind":"choice","label":"Game language","note":"","default":"en-US","options":[["en-US","English"],["zh-Hans","Chinese"]]}]}"#.utf8)
        let settings = try JSONDecoder().decode(SettingsFile.self, from: data).settings
        let translations = ["Play": "\u{5F00}\u{59CB}\u{6E38}\u{620F}"]
        let model = LauncherModel(settings, defaults: defaults, translations: translations)
        let environmentBefore = ProcessInfo.processInfo.environment["DM_LANG"]
        model.uiLanguage = "zh-Hans"
        precondition(model.text("Play") == translations["Play"])
        precondition(model.text("Unknown label") == "Unknown label")
        precondition(model.values["DM_LANG"] == "en-US")
        precondition(defaults.string(forKey: "launcher.DM_LANG") == nil)
        precondition(ProcessInfo.processInfo.environment["DM_LANG"] == environmentBefore)
        model.reset()
        precondition(model.uiLanguage == "zh-Hans")
        let reopened = LauncherModel(settings, defaults: defaults, translations: translations)
        precondition(reopened.uiLanguage == "zh-Hans")
        reopened.uiLanguage = "en"
        precondition(reopened.text("Play") == "Play")
        precondition(defaults.string(forKey: "launcher.uiLanguage") == "en")
        print("UI language independence: PASS")
    }
}
