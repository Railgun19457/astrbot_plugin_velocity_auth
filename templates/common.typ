// VelocityAuth 卡片公共样式：调色板、数据装载与通用块。
#let theme = json(bytes(sys.inputs.at("theme")))
#let payload = json(bytes(sys.inputs.at("payload")))

#let ink = rgb("#1f2933")
#let muted = rgb("#7b8794")
#let border = rgb("#e3e8ef")
#let canvas = rgb("#f4f6fa")
#let accent = rgb("#2563eb")
#let soft-fill = rgb("#f7f9fc")

#let font-families = theme.at("font_families", default: ("Microsoft YaHei", "sans-serif"))
#let page-width = theme.at("page_width", default: 660.0) * 1pt

// 卡片页：宽度固定、高度自适应
#let card-page(body) = {
  set page(width: page-width, height: auto, margin: 18pt, fill: canvas)
  set text(font: font-families, size: 10.5pt, fill: ink)
  set block(spacing: 0.5em)
  body
}

// 一张白色圆角卡片
#let panel(body) = block(
  width: 100%,
  inset: 14pt,
  radius: 10pt,
  fill: white,
  stroke: 0.8pt + border,
  body,
)

#let label-text(body) = text(size: 9.5pt, fill: muted, body)
#let value-text(body) = text(size: 10pt, body)

// 标签 / 取值两列对齐；用 grid 而非逐行 linebreak，行距更紧凑
#let kv-table(rows) = grid(
  columns: (92pt, 1fr),
  column-gutter: 10pt,
  row-gutter: 5pt,
  align: left,
  ..rows
    .map(row => (
      label-text(row.at("label", default: "")),
      value-text(row.at("value", default: "")),
    ))
    .flatten(),
)

// 标题 + 若干 label/value 行
#let section(title, rows, empty: "无数据") = block(width: 100%)[
  #v(7pt)
  #text(size: 11pt, weight: "bold", title)
  #v(4pt)
  #if rows.len() == 0 [
    #label-text(empty)
  ] else {
    kv-table(rows)
  }
  #v(1pt)
]

#let header(data) = block(width: 100%)[
  #text(size: 16pt, weight: "bold", data.at("title", default: ""))
  #if data.at("headline", default: "") != "" [
    #v(2pt)
    #text(size: 12pt, weight: "medium", fill: accent, data.at("headline", default: ""))
  ]
  #if data.at("subtitle", default: "") != "" [
    #v(2pt)
    #label-text(data.at("subtitle", default: ""))
  ]
  #v(3pt)
  #label-text("服务器：" + data.at("server", default: "-"))
]

#let notes-block(notes) = if notes.len() > 0 [
  #v(2pt)
  #notes.map(note => label-text("· " + note)).join(linebreak())
]

#let data-table(table-data) = {
  let columns = table-data.at("columns")
  let rows = table-data.at("rows")
  if rows.len() == 0 {
    label-text(table-data.at("empty", default: "无数据"))
  } else {
    table(
      columns: (1fr,) * columns.len(),
      inset: 5pt,
      stroke: 0.5pt + border,
      align: left + top,
      ..columns.map(column => text(size: 9pt, weight: "bold", fill: muted, column)),
      ..rows.flatten().map(cell => text(size: 9pt, cell)),
    )
  }
}

#let footer(data) = block(width: 100%)[
  #v(6pt)
  #text(size: 8pt, fill: muted, data.at("footer", default: ""))
]
