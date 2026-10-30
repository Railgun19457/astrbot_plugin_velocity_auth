// 玩家信息 / 反查 / 历史等查询结果卡片。
#import "common.typ": *

#show: card-page

#panel[
  #header(payload)

  #for section-data in payload.at("sections", default: ()) [
    #section(
      section-data.at("title", default: ""),
      section-data.at("rows", default: ()),
      empty: section-data.at("empty", default: "无数据"),
    )
  ]

  #for table-data in payload.at("tables", default: ()) [
    #data-table(table-data)
    #v(6pt)
  ]

  #notes-block(payload.at("notes", default: ()))
  #footer(payload)
]
