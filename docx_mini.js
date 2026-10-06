// Generador mínimo de .docx (sin dependencias) para el informe de subtítulos.
// crearDocx({titulo, subtitulo, enlace, filas:[{minuto, imagen:{bytes, ancho, alto}, actual, correccion, tipo, explicacion}]})
// devuelve un Uint8Array con el archivo .docx. Se incrusta en el HTML y también se puede usar desde Node.
(function (raiz) {
  const CRC = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();
  function crc32(b) {
    let c = 0xffffffff;
    for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }
  const utf8 = (s) => new TextEncoder().encode(s);

  // ZIP sin compresión (método 0): suficiente para Word y muy simple.
  function zip(archivos) {
    const partes = [], central = [];
    let off = 0;
    for (const { nombre, datos } of archivos) {
      const n = utf8(nombre), crc = crc32(datos);
      const h = new DataView(new ArrayBuffer(30));
      h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true);
      h.setUint32(14, crc, true); h.setUint32(18, datos.length, true); h.setUint32(22, datos.length, true);
      h.setUint16(26, n.length, true);
      partes.push(new Uint8Array(h.buffer), n, datos);
      const c = new DataView(new ArrayBuffer(46));
      c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
      c.setUint32(16, crc, true); c.setUint32(20, datos.length, true); c.setUint32(24, datos.length, true);
      c.setUint16(28, n.length, true); c.setUint32(42, off, true);
      central.push(new Uint8Array(c.buffer), n);
      off += 30 + n.length + datos.length;
    }
    const tamCentral = central.reduce((a, b) => a + b.length, 0);
    const fin = new DataView(new ArrayBuffer(22));
    fin.setUint32(0, 0x06054b50, true); fin.setUint16(8, archivos.length, true); fin.setUint16(10, archivos.length, true);
    fin.setUint32(12, tamCentral, true); fin.setUint32(16, off, true);
    const todo = [...partes, ...central, new Uint8Array(fin.buffer)];
    const out = new Uint8Array(todo.reduce((a, b) => a + b.length, 0));
    let p = 0;
    for (const t of todo) { out.set(t, p); p += t.length; }
    return out;
  }

  const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  function run(texto, { negrita, color, tam } = {}) {
    const rpr = `<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>` + (negrita ? "<w:b/>" : "") +
      (color ? `<w:color w:val="${color}"/>` : "") + `<w:sz w:val="${tam || 20}"/>`;
    return String(texto ?? "").split("\n").map((l, i) =>
      `${i ? "<w:r><w:br/></w:r>" : ""}<w:r><w:rPr>${rpr}</w:rPr><w:t xml:space="preserve">${esc(l)}</w:t></w:r>`).join("");
  }
  const parrafo = (contenido, extra = "") => `<w:p>${extra ? `<w:pPr>${extra}</w:pPr>` : ""}${contenido}</w:p>`;
  function celda(contenido, ancho, fondo) {
    return `<w:tc><w:tcPr><w:tcW w:w="${ancho}" w:type="dxa"/>${fondo ? `<w:shd w:val="clear" w:color="auto" w:fill="${fondo}"/>` : ""}</w:tcPr>${contenido}</w:tc>`;
  }
  function imagen(rid, id, ancho, alto) {
    const cx = 2300000, cy = Math.round((cx * alto) / ancho); // ~6,4 cm de ancho
    return `<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="${cx}" cy="${cy}"/>` +
      `<wp:docPr id="${id}" name="Captura ${id}"/><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">` +
      `<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">` +
      `<pic:nvPicPr><pic:cNvPr id="${id}" name="captura${id}.jpg"/><pic:cNvPicPr/></pic:nvPicPr>` +
      `<pic:blipFill><a:blip r:embed="${rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>` +
      `<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${cx}" cy="${cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>` +
      `</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>`;
  }

  function crearDocx({ titulo, subtitulo, enlace, filas }) {
    const anchos = [900, 3800, 2600, 2600, 1300, 3700]; // twips; A4 apaisado útil ≈ 14900
    const cab = ["Minuto", "Captura", "Texto actual", "Corrección", "Tipo", "Explicación"];
    const colores = { "Ortografía": "C2410C", "Fidelidad al audio": "7C3AED", "Sincronía": "0F766E" };
    const rels = [], medios = [];
    let filasXml = `<w:tr><w:trPr><w:tblHeader/></w:trPr>${cab.map((c, i) => celda(parrafo(run(c, { negrita: true, tam: 18 })), anchos[i], "EEEEEE")).join("")}</w:tr>`;
    filas.forEach((f, i) => {
      let img = parrafo("");
      if (f.imagen) {
        const rid = `rIdImg${i + 1}`;
        rels.push(`<Relationship Id="${rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/captura${i + 1}.jpg"/>`);
        medios.push({ nombre: `word/media/captura${i + 1}.jpg`, datos: f.imagen.bytes });
        img = parrafo(imagen(rid, i + 1, f.imagen.ancho, f.imagen.alto));
      }
      filasXml += `<w:tr><w:trPr><w:cantSplit/></w:trPr>` + [
        celda(parrafo(run(f.minuto, { negrita: true })), anchos[0]),
        celda(img, anchos[1]),
        celda(parrafo(run(f.actual)), anchos[2]),
        celda(parrafo(run(f.correccion, { negrita: true, color: "166534" })), anchos[3]),
        celda(parrafo(run(f.tipo, { negrita: true, color: colores[f.tipo] || "333333", tam: 18 })), anchos[4]),
        celda(parrafo(run(f.explicacion, { tam: 18 })), anchos[5]),
      ].join("") + "</w:tr>";
    });
    const borde = (n) => `<w:${n} w:val="single" w:sz="4" w:space="0" w:color="BBBBBB"/>`;
    const tabla = `<w:tbl><w:tblPr><w:tblW w:w="${anchos.reduce((a, b) => a + b)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>` +
      `<w:tblBorders>${["top", "left", "bottom", "right", "insideH", "insideV"].map(borde).join("")}</w:tblBorders>` +
      `<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="80" w:type="dxa"/><w:bottom w:w="60" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>` +
      `<w:tblGrid>${anchos.map((a) => `<w:gridCol w:w="${a}"/>`).join("")}</w:tblGrid>${filasXml}</w:tbl>`;
    const enlaceXml = enlace
      ? parrafo(run("Vídeo: ", { tam: 18 }) + `<w:hyperlink r:id="rIdEnlace">${run(enlace, { color: "0B63CE", tam: 18 })}</w:hyperlink>`)
      : "";
    if (enlace) rels.push(`<Relationship Id="rIdEnlace" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="${esc(enlace)}" TargetMode="External"/>`);
    const documento = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"><w:body>` +
      parrafo(run(titulo, { negrita: true, tam: 32 })) + parrafo(run(subtitulo, { color: "666666", tam: 18 })) + enlaceXml + parrafo("") + tabla +
      `<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/><w:pgMar w:top="850" w:right="850" w:bottom="850" w:left="850" w:header="400" w:footer="400" w:gutter="0"/></w:sectPr></w:body></w:document>`;
    return zip([
      { nombre: "[Content_Types].xml", datos: utf8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="jpg" ContentType="image/jpeg"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>`) },
      { nombre: "_rels/.rels", datos: utf8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>`) },
      { nombre: "word/document.xml", datos: utf8(documento) },
      { nombre: "word/_rels/document.xml.rels", datos: utf8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${rels.join("")}</Relationships>`) },
      ...medios,
    ]);
  }

  raiz.crearDocx = crearDocx;
  if (typeof module !== "undefined") module.exports = { crearDocx };
})(typeof window !== "undefined" ? window : globalThis);
