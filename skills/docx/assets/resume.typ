#let resume = json("resume.json")
#set page(paper: "a4", margin: (x: 2cm, y: 1.7cm))
#set text(font: ("Inter", "Noto Sans CJK SC"), size: 10pt, lang: "zh")
#set par(leading: 0.65em)
#set heading(outlined: true)

#align(center, text(size: 20pt, weight: "bold", resume.name))
#align(center, text(resume.contact.join(" | ")))
#if resume.summary != "" [
  #v(6pt)
  #text(resume.summary)
]

#for section in resume.sections [
  #block(above: 10pt, below: 4pt)[
    #heading(level: 1, text(size: 12pt, weight: "bold", section.title))
    #line(length: 100%, stroke: 0.5pt)
  ]
  #for item in section.items [
    #block(above: 3pt, below: 3pt, text(item))
  ]
]
