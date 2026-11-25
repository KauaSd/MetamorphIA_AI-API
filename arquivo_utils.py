"""Utilitários para extração de texto de arquivos enviados (PDF, DOCX, TXT, imagens)
e geração de arquivos .docx de atividades adaptadas pela IA.
"""

from __future__ import annotations

import base64
import copy
import glob
import io
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET
import zipfile

from fastapi import HTTPException, status
from pypdf import PdfReader
import textwrap

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA_TYPE = "application/pdf"
MAX_ARQUIVO_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_DOCX_DESCOMPACTADO_BYTES = 25 * 1024 * 1024  # 25 MB (proteção contra Zip Bomb)
MAX_B64_CHARS = 14_500_000


def decodificar_base64_ou_data_url(conteudo: str) -> bytes:
    """Decodifica uma string base64 pura ou Data URL aplicando limite estrito de tamanho (10 MB)."""
    if not conteudo:
        return b""
    limpo = conteudo.strip()
    if len(limpo) > MAX_B64_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Arquivo enviado excede o limite máximo permitido de 10 MB.",
        )
    if "," in limpo and limpo.lower().startswith("data:"):
        limpo = limpo.split(",", 1)[1].strip()
    faltando = len(limpo) % 4
    if faltando:
        limpo += "=" * (4 - faltando)
    try:
        dados = base64.b64decode(limpo)
    except Exception:
        return b""
    if len(dados) > MAX_ARQUIVO_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Arquivo enviado excede o limite máximo permitido de 10 MB.",
        )
    return dados


def extrair_texto_docx_bytes(conteudo_bytes: bytes) -> str:
    """Extrai todo o texto dos parágrafos de um arquivo .docx com proteção contra Zip/XML Bomb."""
    w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": w_ns}
    with zipfile.ZipFile(io.BytesIO(conteudo_bytes), "r") as zin:
        infos = zin.infolist()
        if len(infos) > 200 or sum(i.file_size for i in infos) > MAX_DOCX_DESCOMPACTADO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Estrutura compactada do arquivo .docx excede os limites seguros.",
            )
        if "word/document.xml" not in zin.namelist():
            return ""
        xml_bytes = zin.read("word/document.xml")

    cabecalho_xml = xml_bytes[:4096].upper()
    if b"<!ENTITY" in cabecalho_xml or b"<!DOCTYPE" in cabecalho_xml:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Arquivo XML/DOCX contém declarações de entidade não permitidas.",
        )

    tree = ET.fromstring(xml_bytes)
    paragrafos: list[str] = []
    for p in tree.findall(".//w:p", ns):
        texto_p = "".join(t.text for t in p.findall(".//w:t", ns) if t.text).strip()
        if texto_p:
            paragrafos.append(texto_p)
    return "\n".join(paragrafos)


def extrair_texto_pdf_bytes(conteudo_bytes: bytes) -> str:
    """Extrai o texto de todas as páginas de um arquivo PDF em memória."""
    leitor = PdfReader(io.BytesIO(conteudo_bytes))
    paginas: list[str] = []
    for idx, pagina in enumerate(leitor.pages, start=1):
        texto = (pagina.extract_text() or "").strip()
        if texto:
            paginas.append(f"[Página {idx}]\n{texto}")
    return "\n\n".join(paginas)


def extrair_texto_arquivo(
    nome_arquivo: str,
    conteudo_bytes: bytes,
    mime_type: str = "",
) -> str:
    """Extrai texto legível de arquivos PDF, DOCX, TXT/MD/CSV ou descreve imagens anexadas."""
    if not conteudo_bytes:
        return f"[Arquivo anexado: {nome_arquivo} (vazio)]"

    nome_lower = (nome_arquivo or "").lower()
    mime_lower = (mime_type or "").lower()

    try:
        if nome_lower.endswith(".pdf") or "pdf" in mime_lower:
            texto = extrair_texto_pdf_bytes(conteudo_bytes)
            if texto.strip():
                return texto.strip()
        if (
            nome_lower.endswith(".docx")
            or "wordprocessingml" in mime_lower
            or "msword" in mime_lower
        ):
            texto = extrair_texto_docx_bytes(conteudo_bytes)
            if texto.strip():
                return texto.strip()
        if (
            nome_lower.endswith((".txt", ".md", ".csv", ".json"))
            or mime_lower.startswith("text/")
        ):
            try:
                return conteudo_bytes.decode("utf-8").strip()
            except UnicodeDecodeError:
                return conteudo_bytes.decode("latin-1", errors="ignore").strip()
        if mime_lower.startswith("image/") or nome_lower.endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".gif")
        ):
            return (
                f"[Imagem de atividade enviada pelo professor: {nome_arquivo} "
                f"({len(conteudo_bytes)} bytes). Adapte a atividade visual/impressa "
                f"com recursos multimodais e passo a passo acessível.]"
            )
    except Exception:
        pass

    # Fallback: tenta decodificar como texto caso não tenha extensão reconhecida
    try:
        texto_tentativa = conteudo_bytes.decode("utf-8").strip()
        if texto_tentativa and any(c.isalpha() for c in texto_tentativa):
            return texto_tentativa
    except Exception:
        pass

    return f"[Arquivo de atividade enviado pelo professor: {nome_arquivo}]"


def _criar_paragrafo_docx(
    texto_linha: str,
    w_ns: str,
    negrito: bool = False,
    tamanho_meio_ponto: str = "24",
    cor_hex: str = "000000",
    com_borda: bool = False,
) -> ET.Element:
    """Cria um elemento <w:p> formatado para documentos Word (.docx)."""
    p = ET.Element(f"{{{w_ns}}}p")
    pPr = ET.SubElement(p, f"{{{w_ns}}}pPr")

    if com_borda:
        pBdr = ET.SubElement(pPr, f"{{{w_ns}}}pBdr")
        for lado, space in (("top", "1"), ("left", "4"), ("bottom", "1"), ("right", "4")):
            b = ET.SubElement(pBdr, f"{{{w_ns}}}{lado}")
            b.set(f"{{{w_ns}}}color", "CCCCCC")
            b.set(f"{{{w_ns}}}space", space)
            b.set(f"{{{w_ns}}}sz", "4")
            b.set(f"{{{w_ns}}}val", "single")

    spacing = ET.SubElement(pPr, f"{{{w_ns}}}spacing")
    spacing.set(f"{{{w_ns}}}after", "120")
    spacing.set(f"{{{w_ns}}}line", "276")
    spacing.set(f"{{{w_ns}}}lineRule", "auto")

    r = ET.SubElement(p, f"{{{w_ns}}}r")
    rPr = ET.SubElement(r, f"{{{w_ns}}}rPr")
    rFonts = ET.SubElement(rPr, f"{{{w_ns}}}rFonts")
    for attr in ("ascii", "cs", "eastAsia", "hAnsi"):
        rFonts.set(f"{{{w_ns}}}{attr}", "Calibri")
    if negrito:
        b_el = ET.SubElement(rPr, f"{{{w_ns}}}b")
        b_el.set(f"{{{w_ns}}}val", "1")
    color = ET.SubElement(rPr, f"{{{w_ns}}}color")
    color.set(f"{{{w_ns}}}val", cor_hex)
    sz = ET.SubElement(rPr, f"{{{w_ns}}}sz")
    sz.set(f"{{{w_ns}}}val", tamanho_meio_ponto)
    szCs = ET.SubElement(rPr, f"{{{w_ns}}}szCs")
    szCs.set(f"{{{w_ns}}}val", tamanho_meio_ponto)

    t = ET.SubElement(r, f"{{{w_ns}}}t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = texto_linha
    return p


def _construir_docx_minimo(xml_document_bytes: bytes) -> bytes:
    """Constrói um pacote .docx OpenXML válido mesmo sem arquivo modelo externo."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("[Content_Types].xml", content_types.encode("utf-8"))
        zout.writestr("_rels/.rels", rels.encode("utf-8"))
        zout.writestr("word/document.xml", xml_document_bytes)
    return buffer.getvalue()


def montar_docx_atividade_adaptada(
    titulo: str,
    nome_aluno: str,
    neurodivergencia: str,
    conteudo_adaptado: str,
    nome_professor: str = "",
    ano_escolar: Optional[str] = None,
) -> bytes:
    """Gera um arquivo .docx formatado contendo a atividade adaptada pela IA."""
    w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": w_ns}

    raiz = Path(__file__).resolve().parent
    arquivos_modelo = glob.glob(str(raiz / "PEI-estilo" / "*MODELO*.docx"))

    paragrafos: list[ET.Element] = [
        _criar_paragrafo_docx(
            "ATIVIDADE PEDAGÓGICA ADAPTADA - METAMORPHIA",
            w_ns,
            negrito=True,
            tamanho_meio_ponto="28",
            cor_hex="1F4E79",
        ),
        _criar_paragrafo_docx(
            f"Título / Tema: {titulo or 'Atividade Adaptada'}",
            w_ns,
            negrito=True,
            tamanho_meio_ponto="24",
        ),
        _criar_paragrafo_docx(
            f"Estudante: {nome_aluno or 'Estudante'} | Perfil / Condição: {neurodivergencia or 'Inclusão'}",
            w_ns,
            negrito=False,
            tamanho_meio_ponto="22",
        ),
    ]
    if nome_professor or ano_escolar:
        info_extra = " | ".join(
            p
            for p in (
                f"Professor(a): {nome_professor}" if nome_professor else "",
                f"Ano/Turma: {ano_escolar}" if ano_escolar else "",
            )
            if p
        )
        paragrafos.append(
            _criar_paragrafo_docx(info_extra, w_ns, negrito=False, tamanho_meio_ponto="22")
        )

    paragrafos.append(_criar_paragrafo_docx(" ", w_ns))

    for linha_bruta in (conteudo_adaptado or "").splitlines():
        linha = linha_bruta.strip()
        if not linha:
            continue
        eh_titulo_secao = (
            linha.startswith("#")
            or (linha.isupper() and len(linha) > 4)
            or (linha.startswith("**") and linha.endswith("**"))
        )
        texto_limpo = linha.lstrip("#").strip().replace("**", "")
        paragrafos.append(
            _criar_paragrafo_docx(
                texto_limpo,
                w_ns,
                negrito=eh_titulo_secao,
                tamanho_meio_ponto="26" if eh_titulo_secao else "24",
                cor_hex="1F4E79" if eh_titulo_secao else "000000",
            )
        )

    if arquivos_modelo:
        try:
            with zipfile.ZipFile(arquivos_modelo[0], "r") as zin:
                xml_bytes = zin.read("word/document.xml")
                arquivos_zip = {
                    name: zin.read(name)
                    for name in zin.namelist()
                    if name != "word/document.xml"
                }
            tree = ET.fromstring(xml_bytes)
            body = tree.find("w:body", ns)
            if body is not None:
                sect_pr = body.find("w:sectPr", ns)
                sect_pr_copy = copy.deepcopy(sect_pr) if sect_pr is not None else None
                body[:] = paragrafos
                if sect_pr_copy is not None:
                    body.append(sect_pr_copy)
                novo_xml = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                    zout.writestr("word/document.xml", novo_xml)
                    for nome_arq, bytes_arq in arquivos_zip.items():
                        zout.writestr(nome_arq, bytes_arq)
                return buffer.getvalue()
        except Exception:
            pass

    doc_el = ET.Element(f"{{{w_ns}}}document")
    body_el = ET.SubElement(doc_el, f"{{{w_ns}}}body")
    for p in paragrafos:
        body_el.append(p)
    xml_final = ET.tostring(doc_el, encoding="utf-8", xml_declaration=True)
    return _construir_docx_minimo(xml_final)


def para_data_url_docx(docx_bytes: bytes) -> str:
    """Converte os bytes de um .docx para Data URL pronta para download no frontend."""
    b64 = base64.b64encode(docx_bytes).decode("ascii")
    return f"data:{DOCX_MEDIA_TYPE};base64,{b64}"


def _escapar_texto_pdf(texto: str) -> bytes:
    """Codifica texto em CP1252 (WinAnsiEncoding) e escapa parênteses/barras para stream PDF."""
    normalizado = (
        (texto or "")
        .replace("—", "-")
        .replace("–", "-")
        .replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
        .replace("‘", "'")
        .replace("•", "-")
    )
    raw = normalizado.encode("cp1252", errors="replace")
    escapado = (
        raw.replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
    )
    return escapado


def montar_pdf_atividade_adaptada(
    titulo: str,
    nome_aluno: str,
    neurodivergencia: str,
    conteudo_adaptado: str,
    nome_professor: str = "",
    ano_escolar: Optional[str] = None,
) -> bytes:
    """Gera um arquivo PDF 1.4 válido em Python puro contendo a atividade adaptada pela IA."""
    linhas_render: list[tuple[str, bool, int]] = [
        ("ATIVIDADE PEDAGÓGICA ADAPTADA - METAMORPHIA", True, 14),
        (f"Título / Tema: {titulo or 'Atividade Adaptada'}", True, 12),
        (
            f"Estudante: {nome_aluno or 'Estudante'} | Perfil / Condição: {neurodivergencia or 'Inclusão'}",
            False,
            10,
        ),
    ]
    if nome_professor or ano_escolar:
        info_extra = " | ".join(
            p
            for p in (
                f"Professor(a): {nome_professor}" if nome_professor else "",
                f"Ano/Turma: {ano_escolar}" if ano_escolar else "",
            )
            if p
        )
        linhas_render.append((info_extra, False, 10))
    linhas_render.append(("", False, 10))

    for linha_bruta in (conteudo_adaptado or "").splitlines():
        linha = linha_bruta.strip()
        if not linha:
            linhas_render.append(("", False, 10))
            continue
        eh_titulo = (
            linha.startswith("#")
            or (linha.isupper() and len(linha) > 4)
            or (linha.startswith("**") and linha.endswith("**"))
        )
        texto_limpo = linha.lstrip("#").strip().replace("**", "")
        largura = 72 if eh_titulo else 82
        for parte in textwrap.wrap(texto_limpo, width=largura) or [""]:
            linhas_render.append((parte, eh_titulo, 12 if eh_titulo else 11))

    # Paginação simples (A4: 595 x 842 pt)
    paginas_streams: list[bytes] = []
    y_atual = 790
    comandos_pagina: list[bytes] = []

    for texto_l, negrito, tamanho in linhas_render:
        passo = tamanho + 6
        if y_atual - passo < 50:
            paginas_streams.append(b"\n".join(comandos_pagina))
            comandos_pagina = []
            y_atual = 790
        if texto_l:
            fonte = b"/F2" if negrito else b"/F1"
            esc = _escapar_texto_pdf(texto_l)
            comandos_pagina.append(
                b"BT "
                + fonte
                + f" {tamanho} Tf 50 {y_atual} Td (".encode("ascii")
                + esc
                + b") Tj ET"
            )
        y_atual -= passo

    if comandos_pagina or not paginas_streams:
        paginas_streams.append(b"\n".join(comandos_pagina))

    # Montagem da árvore de objetos PDF 1.4
    # Obj 1: Catalog, Obj 2: Pages, Obj 3: Font F1 (Helvetica), Obj 4: Font F2 (Helvetica-Bold)
    objetos: list[bytes] = []
    num_paginas = len(paginas_streams)
    kids_refs = " ".join(f"{5 + i * 2} 0 R" for i in range(num_paginas))

    objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objetos.append(
        f"<< /Type /Pages /Kids [{kids_refs}] /Count {num_paginas} >>".encode("ascii")
    )
    objetos.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    objetos.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )

    for i, stream_bytes in enumerate(paginas_streams):
        page_obj_id = 5 + i * 2
        content_obj_id = page_obj_id + 1
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {content_obj_id} 0 R >>"
        ).encode("ascii")
        content_dict = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream"
        )
        objetos.append(page_dict)
        objetos.append(content_dict)

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for idx, obj_body in enumerate(objetos, start=1):
        offsets.append(buf.tell())
        buf.write(f"{idx} 0 obj\n".encode("ascii"))
        buf.write(obj_body)
        buf.write(b"\nendobj\n")

    xref_pos = buf.tell()
    total_objs = len(objetos) + 1
    buf.write(f"xref\n0 {total_objs}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(
        f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return buf.getvalue()


def para_data_url_pdf(pdf_bytes: bytes) -> str:
    """Converte os bytes de um .pdf para Data URL pronta para download no frontend."""
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return f"data:{PDF_MEDIA_TYPE};base64,{b64}"
