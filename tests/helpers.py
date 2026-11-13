"""行为测试共用的轻量测试数据构造函数，不保存业务状态。"""

import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def pdf_bytes(text=None):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def upload_pdf(service, p, kb_id, body):
    ticket = service.run(
        p,
        "prepare_document_upload",
        {"knowledge_base_id": kb_id, "filename": "source.pdf", "size": len(body)},
    )
    service.receive_upload(ticket["upload_url"].rsplit("/", 1)[1], body)
    return service.run(
        p,
        "create_document",
        {"knowledge_base_id": kb_id, "name": "source.pdf", "upload_id": ticket["upload_id"]},
    )
