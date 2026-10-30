// 服务器统计卡片：KPI 网格 + 分组明细。
#import "common.typ": *

#show: card-page

#panel[
  #header(payload)

  #let kpis = payload.at("kpis", default: ())
  #if kpis.len() > 0 [
    #v(4pt)
    #grid(
      columns: (1fr,) * kpis.len(),
      column-gutter: 10pt,
      ..kpis.map(kpi => block(
        width: 100%,
        inset: (x: 8pt, top: 8pt, bottom: 8pt),
        radius: 8pt,
        fill: soft-fill,
        stroke: 0.6pt + border,
        grid(
          columns: (1fr,),
          row-gutter: 2pt,
          align(center, text(size: 17pt, weight: "bold", fill: accent, kpi.at("value", default: "-"))),
          align(center, text(size: 9pt, fill: muted, kpi.at("label", default: ""))),
        ),
      )),
    )
  ]

  #v(4pt)
  #let sections = payload.at("sections", default: ())
  #if sections.len() > 0 [
    #grid(
      columns: (1fr, 1fr),
      column-gutter: 16pt,
      row-gutter: 2pt,
      ..sections.map(section-data => section(
        section-data.at("title", default: ""),
        section-data.at("rows", default: ()),
        empty: section-data.at("empty", default: "无数据"),
      )),
    )
  ]

  #notes-block(payload.at("notes", default: ()))
  #footer(payload)
]
