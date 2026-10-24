"""Printable 1:1 ChArUco calibration board PDFs.

Ported from ``generate_charuco_disc.py`` and ``generate_rectangular_charuco.py``:

- ``build_turntable_pdf``: full-coverage 8-inch (203.2 mm) circular disc,
  10x10 grid of 20.0 mm squares, with a dashed cut outline and a 100 mm
  calibration check ruler.
- ``build_rectangular_pdf``: full-page edge-to-edge boards, 10x13 (US Letter)
  or 10x14 (A4), also with a 100 mm check ruler.

Print at 100% ("Actual Size") and verify the ruler with calipers before use.
"""
import os
import tempfile

import cv2
import numpy as np
from reportlab.lib.colors import black, red, HexColor
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .charuco import (SQUARE_LENGTH_MM, MARKER_LENGTH_MM, SQUARES_X, SQUARES_Y,
                      make_board)

TURNTABLE_DIA_INCHES = 8.0
TURNTABLE_DIA_MM = TURNTABLE_DIA_INCHES * 25.4  # 203.2 mm
TURNTABLE_RADIUS_PT = (TURNTABLE_DIA_INCHES / 2.0) * 72.0  # 288.0 pt
GRID_SIZE_MM = SQUARES_X * SQUARE_LENGTH_MM  # 200.0 mm
GRID_SIZE_PT = (GRID_SIZE_MM / 25.4) * 72.0  # ~566.93 pt

PAGE_SIZES = {'letter': (letter, 'US Letter (8.5 x 11 in)'),
              'a4': (A4, 'A4 (210 x 297 mm)')}


def _circular_board_bitmap(temp_path):
    """Render the 10x10 grid clipped to the exact 8-inch circle (transparent outside)."""
    board, _ = make_board(SQUARES_X, SQUARES_Y)
    img_px = 3000
    board_img = board.generateImage((img_px, img_px), marginSize=0, borderBits=1)

    px_per_mm = img_px / GRID_SIZE_MM
    radius_px = (TURNTABLE_DIA_MM / 2.0) * px_per_mm
    h, w = board_img.shape
    y_grid, x_grid = np.ogrid[:h, :w]
    dist_from_center = np.sqrt((x_grid - w / 2.0) ** 2 + (y_grid - h / 2.0) ** 2)

    rgba = cv2.cvtColor(board_img, cv2.COLOR_GRAY2BGRA)
    rgba[dist_from_center > radius_px] = [255, 255, 255, 0]
    cv2.imwrite(temp_path, rgba)
    return temp_path


def _draw_check_ruler(c, center_x, ruler_y, ruler_len_mm=100.0, ticks=10, font_size=7.5):
    ruler_len_pt = (ruler_len_mm / 25.4) * 72.0
    ruler_x1 = center_x - (ruler_len_pt / 2.0)
    ruler_x2 = ruler_x1 + ruler_len_pt

    c.setStrokeColor(black)
    c.setLineWidth(1.5)
    c.line(ruler_x1, ruler_y, ruler_x2, ruler_y)
    c.line(ruler_x1, ruler_y - 7, ruler_x1, ruler_y + 7)
    c.line(ruler_x2, ruler_y - 7, ruler_x2, ruler_y + 7)

    c.setLineWidth(0.8)
    for i in range(ticks, int(ruler_len_mm), ticks):
        tx = ruler_x1 + (i / 25.4) * 72.0
        c.line(tx, ruler_y - 4, tx, ruler_y + 4)
        c.setFont('Helvetica', font_size)
        c.drawCentredString(tx, ruler_y - 12, f'{i}')


def build_turntable_pdf(output_path, page_size=letter, paper_name='US Letter (8.5 x 11 in)'):
    """Create the 8-inch turntable disc PDF at 1:1 scale and return its path."""
    page_w, page_h = page_size
    center_x = page_w / 2.0
    center_y = page_h - (4.65 * 72.0)

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        temp_img_path = tmp.name
    try:
        _circular_board_bitmap(temp_img_path)
        c = canvas.Canvas(str(output_path), pagesize=page_size)
        c.setTitle('8-Inch Turntable ChArUco Calibration Disc (Full Coverage)')

        grid_x = center_x - (GRID_SIZE_PT / 2.0)
        grid_y = center_y - (GRID_SIZE_PT / 2.0)
        c.drawImage(temp_img_path, grid_x, grid_y, width=GRID_SIZE_PT, height=GRID_SIZE_PT,
                    mask='auto')

        # Dashed 8.0-inch circular cut line
        c.setStrokeColor(HexColor('#333333'))
        c.setLineWidth(1.4)
        c.setDash([6, 4])
        c.circle(center_x, center_y, TURNTABLE_RADIUS_PT)
        c.setDash([])

        # Center alignment mark
        c.setStrokeColor(red)
        c.setLineWidth(1.0)
        cross_size = 5.0 * mm
        c.circle(center_x, center_y, 1.5 * mm)
        c.line(center_x - cross_size, center_y, center_x + cross_size, center_y)
        c.line(center_x, center_y - cross_size, center_x, center_y + cross_size)

        c.setFont('Helvetica-Bold', 8.5)
        c.setFillColor(HexColor('#444444'))
        c.drawCentredString(center_x, center_y + TURNTABLE_RADIUS_PT + 7,
                            '--- 8.0 INCH (203.2 mm) TURNTABLE CUT OUTLINE (DASHED) ---')

        ruler_y = center_y - TURNTABLE_RADIUS_PT - (16.0 * mm)
        _draw_check_ruler(c, center_x, ruler_y)
        c.setFont('Helvetica-Bold', 9.0)
        c.setFillColor(black)
        c.drawCentredString(center_x, ruler_y + 9,
                            '<--- CALIBRATION CHECK: EXACTLY 100.0 mm (10.0 cm) --->')

        instr_y = ruler_y - (20.0 * mm)
        c.setFont('Helvetica-Bold', 9.0)
        c.drawString(45, instr_y, 'PRINTING & SETUP INSTRUCTIONS:')
        c.setFont('Helvetica', 8.0)
        c.setFillColor(HexColor('#222222'))
        c.drawString(45, instr_y - 11,
                     '1. PRINT SCALE: In your print dialog, select "Actual Size" or set Page Scaling to 100%.')
        c.drawString(45, instr_y - 21,
                     '   WARNING: DO NOT select "Fit to Page", "Scale to Fit", or "Shrink Oversized Pages".')
        c.drawString(45, instr_y - 31,
                     '2. MEASURE CHECK: Measure the calibration ruler above with calipers. It MUST be exactly 100.0 mm.')
        c.drawString(45, instr_y - 41,
                     '3. CUT & MOUNT: Cut along the circular dashed outline and glue/tape flat to your 8-inch turntable disc.')
        c.drawString(45, instr_y - 51,
                     '4. SPECS: Full-bleed 10x10 pattern (20.0 mm squares, 15.0 mm markers) covering the 8-inch disc.')

        c.showPage()
        c.save()
    finally:
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)
    return str(output_path)


def build_rectangular_pdf(output_path, page_size=letter, squares_x=10, squares_y=13,
                          paper_name='US Letter (8.5 x 11 in)'):
    """Create a full-page rectangular ChArUco board PDF at 1:1 scale and return its path."""
    page_w, page_h = page_size
    center_x = page_w / 2.0

    grid_w_mm = squares_x * SQUARE_LENGTH_MM
    grid_h_mm = squares_y * SQUARE_LENGTH_MM
    grid_w_pt = (grid_w_mm / 25.4) * 72.0
    grid_h_pt = (grid_h_mm / 25.4) * 72.0

    board, _ = make_board(squares_x, squares_y)
    board_img = board.generateImage((int(squares_x * 300), int(squares_y * 300)),
                                    marginSize=0, borderBits=1)
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        temp_img_path = tmp.name
    try:
        cv2.imwrite(temp_img_path, board_img)

        c = canvas.Canvas(str(output_path), pagesize=page_size)
        c.setTitle(f'Full-Page Rectangular ChArUco Calibration Board ({paper_name})')

        top_margin_pt = 18.0
        grid_x = center_x - (grid_w_pt / 2.0)
        grid_y = page_h - top_margin_pt - grid_h_pt
        c.drawImage(temp_img_path, grid_x, grid_y, width=grid_w_pt, height=grid_h_pt)

        c.setStrokeColor(HexColor('#222222'))
        c.setLineWidth(1.2)
        c.rect(grid_x, grid_y, grid_w_pt, grid_h_pt)

        ruler_y = grid_y - 10.0
        _draw_check_ruler(c, center_x, ruler_y, ticks=20, font_size=7.0)
        c.setFont('Helvetica-Bold', 7.0)
        c.drawCentredString(center_x, ruler_y + 4,
                            '<--- 100.0 mm CALIBRATION CHECK (EXACTLY 5 SQUARES) --->')

        c.setFont('Helvetica', 6.5)
        c.setFillColor(HexColor('#444444'))
        c.drawCentredString(
            center_x, ruler_y - 8,
            f'{paper_name} FULL-PAGE: {squares_x}x{squares_y} grid | '
            f'{SQUARE_LENGTH_MM:.1f} mm squares | Print at 100% (Actual Size)')

        c.showPage()
        c.save()
    finally:
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)
    return str(output_path)


def build_pdfs(output_dir, kind='both', paper_format='both'):
    """Generate the requested board PDFs into ``output_dir``; return their paths."""
    output_dir = str(output_dir)
    results = []
    papers = ['letter', 'a4'] if paper_format == 'both' else [paper_format]

    if kind in ('turntable', 'both'):
        for paper in papers:
            page_size, name = PAGE_SIZES[paper]
            path = os.path.join(output_dir, f'charuco_8in_turntable_{paper}.pdf')
            results.append(build_turntable_pdf(path, page_size, name))
    if kind in ('rectangular', 'both'):
        for paper in papers:
            page_size, name = PAGE_SIZES[paper]
            if paper == 'a4':
                path = os.path.join(output_dir, 'charuco_rectangular_a4.pdf')
                results.append(build_rectangular_pdf(path, page_size, 10, 14, name))
            else:
                path = os.path.join(output_dir, 'charuco_rectangular_letter.pdf')
                results.append(build_rectangular_pdf(path, page_size, 10, 13, name))
    return results
