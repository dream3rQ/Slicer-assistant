// Colours and type from the designers' Figma (docs/slicer-ui.md, spec in
// project files: slicer-ui/figma/spec.md). Every view creates one: Style { id: st }.
import QtQuick 2.7

QtObject {
    readonly property color accent: "#1676D2"
    readonly property color accentDeep: "#0475C8"
    readonly property color accentSoft: "#DEECF9"
    readonly property color borderAccent: "#A9D3F8"
    readonly property color border: "#DCE4ED"
    readonly property color textPrimary: "#203348"
    readonly property color textSecondary: "#6B7E91"
    readonly property color iconMuted: "#93A2B2"
    readonly property color textInk: "#292727"
    readonly property color placeholder: Qt.rgba(41 / 255, 39 / 255, 39 / 255, 0.5)
    readonly property color groupBg: "#F6F8FB"
    readonly property color tertiaryBg: "#E5EBF1"
    readonly property color danger: "#BD4A4F"
    readonly property color dangerBg: "#FFF0F1"
    readonly property color warning: "#9A6A00"
    readonly property color success: "#26806A"
    readonly property color micBg: Qt.rgba(171 / 255, 171 / 255, 171 / 255, 0.2)
    readonly property color inputBorder: Qt.rgba(0, 0, 0, 0.1)

    // Inter is bundled (fonts/, loaded by slicer_ui.load_fonts); the mock-up's SF Pro
    // is not available on Windows, so the panel uses Inter too.
    readonly property string family: Qt.fontFamilies().indexOf("Inter") >= 0 ? "Inter" : Qt.application.font.family
}
