"""A real deterministic empty native page for offline parser fixtures."""


def blank_native_pdf():
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources <<>> /Contents 4 0 R >>',
        b'<< /Length 0 >>\nstream\n\nendstream']
    raw, offsets = bytearray(b'%PDF-1.7\n'), [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(raw))
        raw.extend(str(number).encode() + b' 0 obj\n' + value + b'\nendobj\n')
    start = len(raw)
    raw.extend(b'xref\n0 5\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        raw.extend(f'{offset:010d} 00000 n \n'.encode())
    raw.extend(b'trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n')
    return bytes(raw)
