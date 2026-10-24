// An SVG from qml/icons (exported from the Figma mock-up), drawn sharp at any size.
import QtQuick 2.7

Image {
    property string name: ""
    source: name ? "icons/" + name + ".svg" : ""
    sourceSize.width: Math.ceil(width * 2)
    sourceSize.height: Math.ceil(height * 2)
    fillMode: Image.PreserveAspectFit
    smooth: true
    mipmap: true
}
