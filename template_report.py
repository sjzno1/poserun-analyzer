"""
PoseRun Analyzer - 样板格式报告生成器
按照《2025姿势跑法夏训营学员技术分析评测》样板排版：
  第1页：标题 + 姓名/教练/得分/日期 + 环节|分项|评判结果|改进建议 表格
  第2页：关键帧照片（上3下4）+ 图注 + 总结建议语
生成 PDF（reportlab，内置 STSong-Light 中文字体，无需字体文件）
"""

import os
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                Table, TableStyle, Image as RLImage)
from PIL import Image as PILImage

FONT = 'STSong-Light'


def _ensure_font():
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))


def generate_template_report(data: dict, keyframes: list,
                             output_path: str) -> str:
    """
    data: {
        'title': 报告标题,
        'name': 姓名, 'coach': 评分教练, 'score': 得分(int), 'date': 日期字符串,
        'rows': [(环节, 分项, 评判结果, 改进建议), ...],
        'summary': 总结建议语,
    }
    keyframes: [(图片路径, 图注), ...]  最多7张（上3下4）
    """
    _ensure_font()

    st_title = ParagraphStyle('t', fontName=FONT, fontSize=16, leading=22,
                              alignment=TA_CENTER)
    st_info = ParagraphStyle('i', fontName=FONT, fontSize=10, leading=14,
                             alignment=TA_CENTER)
    st_cell = ParagraphStyle('c', fontName=FONT, fontSize=8.5, leading=12)
    st_cell_c = ParagraphStyle('cc', fontName=FONT, fontSize=8.5, leading=12,
                               alignment=TA_CENTER)
    st_h1 = ParagraphStyle('h1', fontName=FONT, fontSize=12, leading=16,
                           spaceBefore=8, spaceAfter=4)
    st_cap = ParagraphStyle('cap', fontName=FONT, fontSize=8, leading=11,
                            alignment=TA_CENTER,
                            textColor=colors.HexColor('#444444'))
    st_sum = ParagraphStyle('sum', fontName=FONT, fontSize=10, leading=15,
                            textColor=colors.HexColor('#C00000'))

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        title=data.get('title', '跑姿技术评测报告'),
        author=data.get('coach', ''))

    story = []

    # ---------------- 第1页：标题 + 信息行 + 评分表 ----------------
    story.append(Paragraph(data.get('title', '跑姿技术评测报告'), st_title))
    story.append(Spacer(1, 6))
    info = (f"姓名：{data.get('name', '')}　　评分教练：{data.get('coach', '')}"
            f"　　得分：{data.get('score', '')}　　日期：{data.get('date', '')}")
    story.append(Paragraph(info, st_info))
    story.append(Spacer(1, 8))

    rows = data.get('rows', [])
    table_rows = [[Paragraph('环节', st_cell_c), Paragraph('分项', st_cell_c),
                   Paragraph('评判结果', st_cell_c),
                   Paragraph('改进建议', st_cell_c)]]
    for cat, item, result, sugg in rows:
        table_rows.append([
            Paragraph(cat, st_cell_c), Paragraph(item, st_cell_c),
            Paragraph(result, st_cell), Paragraph(sugg, st_cell),
        ])

    t = Table(table_rows, colWidths=[2.0 * cm, 2.6 * cm, 3.4 * cm, 9.2 * cm])
    style = [
        ('FONTNAME', (0, 0), (-1, -1), FONT),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8E8E8')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    # 环节列纵向合并（相邻同环节合并）
    i = 1
    while i < len(table_rows):
        j = i
        while (j + 1 < len(table_rows)
               and table_rows[j + 1][0].text == table_rows[i][0].text):
            j += 1
        if j > i:
            style.append(('SPAN', (0, i), (0, j)))
        i = j + 1
    t.setStyle(TableStyle(style))
    story.append(t)

    # ---------------- 第2页：关键帧照片 + 总结 ----------------
    story.append(Spacer(1, 14))
    if keyframes:
        story.append(Paragraph('关键跑姿分析', st_h1))

        def img_cell(path, caption, width_cm):
            try:
                with PILImage.open(path) as im:
                    iw, ih = im.size
            except Exception:
                iw, ih = 300, 560
            w = width_cm * cm
            h = w * ih / iw
            cell = [RLImage(path, width=w, height=h)]
            if caption:
                cell.append(Spacer(1, 2))
                cell.append(Paragraph(caption, st_cap))
            return cell

        top = keyframes[:3]
        bottom = keyframes[3:7]

        if top:
            per = 15.6 / len(top)
            grid = [[img_cell(p, c, per - 0.3) for p, c in top]]
            gt = Table(grid, colWidths=[per * cm] * len(top))
            gt.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ]))
            story.append(gt)
            story.append(Spacer(1, 6))

        if bottom:
            per = 15.6 / len(bottom)
            grid = [[img_cell(p, c, per - 0.3) for p, c in bottom]]
            gb = Table(grid, colWidths=[per * cm] * len(bottom))
            gb.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ]))
            story.append(gb)

    summary = data.get('summary', '')
    if summary:
        story.append(Spacer(1, 10))
        story.append(Paragraph(summary, st_sum))

    doc.build(story)
    return output_path


# ---------------------------------------------------------------------- #
# 样板数据（2025夏训营 第一周期 孙建忠）——用于生成示例报告
# ---------------------------------------------------------------------- #
SAMPLE_DATA = {
    'title': '2025 姿势跑法夏训营学员技术分析评测',
    'name': '孙建忠',
    'coach': '亚新',
    'score': 71,
    'date': '2025-08-01',
    'rows': [
        ('落地', '脚刚触地位置', '轻度的脚跟触地',
         '勾脚会让胫骨前肌疲劳，同时让触地时间延长，日常需要主动松解小腿前侧及'
         '多活动脚踝，通过光脚原地跑感受脚踝放松自然落地的感受，同时可以多做踮脚走、'
         '原地并腿小跳，感受前脚掌自然落地的知觉。'),
        ('落地', '膝关节', '脚刚触地位置膝盖有微弯', ''),
        ('落地', '跨步', '中度跨步',
         '跨步会导致落脚点远离身体重心，膝盖脚踝和小腿前侧落地冲击力大，同时触地'
         '时间被主动延长，无法充分利用肌肉肌腱的弹性，跑步效率下降。建议主动缩小'
         '步幅，脚不要往前伸，多进行185以上高步频的原地跑及上拉训练，找到脚落在'
         '身体正下方、一落地就马上拉起脚掌的知觉。无论配速快慢，脚底下都要保持紧凑'
         '的换脚节奏，不要黏在地上。'),
        ('落地', '落地框架', '两个膝盖有重合',
         '落地时注意收紧身体框架，保持两膝方向一致。'),
        ('落地', '小腿角度', '小腿地面夹角较好', ''),
        ('落地', '推蹬、重心起伏', '有明显推蹬',
         '推蹬会使得小腿和跟腱主动发力持续紧张，从而容易受伤；而且由于两脚的活塞'
         '机制，后脚留在地上去推蹬就会使得前脚往前出去，落地位置在重心前面，触地'
         '时间增加而效率下降。因此需要专注上拉的技术训练，通过关键跑姿-点地上拉-'
         '高步频原地跑，找到落地后不推蹬地面、只专心把支撑脚拉离地面完成换脚的'
         '知觉。另外加强小腿后侧的松解，跑的时候小腿和脚掌脚踝要放松，不要主动发力，'
         '把关注点全部放在落地就拉起脚掌。'),
        ('离地', '伸腿', '摆荡腿前伸早，重力利用不足',
         '摆荡腿前伸早，通常是因为前倾不足、对失重有恐惧。在平时训练的时候要多培养'
         '前倾带动身体向前的知觉，尽量维持关键跑姿的姿态，掌握好上拉的时机，更加'
         '充分地利用好重力这一推进力。'),
        ('上身', '摆臂', '摆臂基本正常', ''),
        ('上身', '晃动', '身体没有明显晃动', ''),
        ('身体姿态', '整体姿态', '进入关键跑姿时身体一条直线，整体姿态好', ''),
        ('整体', '力线', '正常', ''),
        ('整体', '弹性', '基本正常', ''),
        ('整体', '流畅度', '动作基本流畅', ''),
    ],
    'summary': ('注意落地收紧身体框架，加快上拉，落地脚踝放松，避免主动跨步和推蹬。'
                '跑动中体会重心带动身体向前的知觉。'),
}


if __name__ == '__main__':
    import sys
    img_dir = sys.argv[1] if len(sys.argv) > 1 else \
        '/Users/sjz/.hermes/cache/scratch/sjz_imgs'
    out = sys.argv[2] if len(sys.argv) > 2 else \
        '/Users/sjz/.hermes/cache/scratch/2025姿势跑法夏训营学员技术分析评测_孙建忠_示例.pdf'

    photo_files = ['p0_img1.png', 'p0_img2.png', 'p0_img3.png', 'p0_img4.png',
                   'p0_img5.png', 'p0_img8.png', 'p0_img9.png']
    captions = ['正面-力线正常', '背面-力量线正常', '比较明显的足跟触地，膝关节压力大',
                '中度跨步，脚落实在身体重心前', '关键跑姿，上拉不足',
                '离地有推蹬，影响拉起速度', '摆荡腿前伸时机过早，整体框架松，重力利用不充分']
    keyframes = [(os.path.join(img_dir, f), c)
                 for f, c in zip(photo_files, captions)
                 if os.path.exists(os.path.join(img_dir, f))]
    path = generate_template_report(SAMPLE_DATA, keyframes, out)
    print('OK:', path, os.path.getsize(path), 'bytes')
