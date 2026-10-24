// One filter of the "Память агента" window: a caption over a list. The first
// choice is always "все" (value ""); `options` are [{ value, label }].
import QtQuick 2.7
import QtQuick.Controls 1.4
import QtQuick.Controls.Styles 1.4

Column {
    id: filter
    property string title: ""
    property var options: []
    property string value: ""
    property string allLabel: "все"
    signal picked(string value)
    onValueChanged: sync()
    function sync() {
        var index = 0;
        for (var i = 0; i < choices.length; i++) if (String(choices[i].value) === value) index = i;
        if (combo.currentIndex !== index) combo.currentIndex = index;
    }
    Style { id: st }
    spacing: 4
    width: 150

    property var choices: [{ value: "", label: allLabel }].concat(options)   // set directly for a list without "все"

    Text {
        text: filter.title.toUpperCase()
        color: st.textSecondary
        font.family: st.family; font.pixelSize: 9; font.weight: Font.DemiBold
    }
    Rectangle {
        width: filter.width; height: 28; radius: 5
        color: "white"
        border.width: 1
        border.color: filter.value !== "" ? st.borderAccent : st.border
        ComboBox {
            id: combo
            anchors.fill: parent
            model: filter.choices.map(function (o) { return o.label })
            // set by hand: Controls 1 puts currentIndex back to 0 when the model changes
            onModelChanged: filter.sync()
            Component.onCompleted: filter.sync()
            onActivated: filter.picked(String(filter.choices[index].value))
            style: ComboBoxStyle {
                background: Item {
                    Icon { anchors.right: parent.right; anchors.rightMargin: 9; anchors.verticalCenter: parent.verticalCenter; name: "chevron_down"; width: 10; height: 10 }
                }
                label: Text {
                    leftPadding: 3
                    rightPadding: 14
                    text: control.currentText
                    color: filter.value !== "" ? st.accent : st.textPrimary
                    font.family: st.family; font.pixelSize: 12; font.weight: filter.value !== "" ? Font.Medium : Font.Normal
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
            }
        }
    }
}
