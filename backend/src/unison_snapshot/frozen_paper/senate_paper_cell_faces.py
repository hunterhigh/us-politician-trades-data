"""Closed original source cell faces, allowing curved scanned form rules."""
import hashlib


def enclosed_cell_ink(image, left, right, top, bottom):
    """Return every enclosed dark pixel; never reconnect or erase source ink.

    Four-connected white flood fill must remain inside one calibrated cell.
    A second exterior flood fill identifies its enclosed islands. The source
    grayscale values, rather than a cleaned or reconstructed glyph, are kept.
    """
    from PIL import Image, ImageDraw
    box = [left - 30, top - 30, right + 30, bottom + 30]
    if min(box) < 0 or box[2] > image.width or box[3] > image.height:
        return None
    gray = image.convert("L").crop(tuple(box))
    face = gray.point(lambda value: 255 if value >= 160 else 0)
    center = ((left + right) // 2 - box[0], (top + bottom) // 2 - box[1])
    if face.getpixel(center) != 255:
        # A central X may occupy the geometric center. Choose a deterministic
        # white seed to its side; the same closed-face checks still apply.
        center = (left + (right-left)//4 - box[0], center[1])
        if face.getpixel(center) != 255:
            return None
    ImageDraw.floodfill(face, center, 128)
    face_mask = face.point(lambda value: 255 if value == 128 else 0)
    bounds = face_mask.getbbox()
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    global_bounds = [x0 + box[0], y0 + box[1], x1 + box[0], y1 + box[1]]
    count = face_mask.histogram()[255]
    if (x0 == 0 or y0 == 0 or x1 == gray.width or y1 == gray.height or
            abs(global_bounds[0] - left) > 8 or abs(global_bounds[2] - right) > 8 or
            abs(global_bounds[1] - top) > 24 or abs(global_bounds[3] - bottom) > 24 or
            count < .5 * (right - left) * (bottom - top)):
        return None
    outside = face_mask.point(lambda value: 0 if value else 255)
    ImageDraw.floodfill(outside, (0, 0), 128)
    # A mark connected to a rule must not disappear with that rule. Any
    # exterior-connected dark stroke surrounded by this face on both axes
    # makes the separation ambiguous. Curved outer boundaries are preserved.
    rows = {}
    columns = {}
    for y in range(y0, y1):
        xs = [x for x in range(x0, x1) if face_mask.getpixel((x, y))]
        if xs:
            rows[y] = (xs[0], xs[-1])
    for x in range(x0, x1):
        ys = [y for y in range(y0, y1) if face_mask.getpixel((x, y))]
        if ys:
            columns[x] = (ys[0], ys[-1])
    # A calibrated form rule may slope gradually; a stroke projecting into
    # the face creates a local contour jump. Do not absorb that stroke as a
    # boundary. Exclude only the already bounded rule-junction corners.
    profiles = [
        [rows[y][side] for y in range(top+24-box[1], bottom-24-box[1]) if y in rows]
        for side in (0, 1)
    ] + [
        [columns[x][side] for x in range(left+8-box[0], right-8-box[0]) if x in columns]
        for side in (0, 1)
    ]
    if any(not values or any(abs(a-b) > 2 for a,b in zip(values, values[1:])) for values in profiles):
        return None
    ink = Image.new("L", gray.size, 255)
    for y in range(gray.height):
        for x in range(gray.width):
            if gray.getpixel((x, y)) < 160:
                if outside.getpixel((x, y)) == 255:
                    ink.putpixel((x, y), gray.getpixel((x, y)))
                elif (y in rows and x in columns and rows[y][0] < x < rows[y][1] and
                      columns[x][0] < y < columns[x][1]):
                    return None
    return ink, {
        "source_box_px": box,
        "source_crop_grayscale_sha256": hashlib.sha256(gray.tobytes()).hexdigest(),
        "closed_face_bounds_px": global_bounds,
        "closed_face_pixel_count": count,
        "closed_face_mask_sha256": hashlib.sha256(face_mask.tobytes()).hexdigest(),
        "enclosed_ink_sha256": hashlib.sha256(ink.tobytes()).hexdigest(),
        "all_enclosed_dark_pixels_retained": True,
        "source_ink_reconnected": False,
    }
