"""Streaming CVAT-for-images indexer, with no DOM or external resource access."""
import json
from xml.parsers import expat

from datasets.limits import ValidationError


def parse_xml(file, database, limits):
    if expat.version_info < (2,6,0):
        raise ValidationError("EXPAT_SECURITY_VERSION")
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack, nodes = [], 0
    counts = {"image":0,"box":0,"tag":0}
    image_ids = set()

    def forbidden(*args):
        raise ValidationError("XML_DTD_OR_ENTITY_FORBIDDEN")
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.ProcessingInstructionHandler = forbidden

    def start(tag, attrs):
        nonlocal nodes
        nodes += 1
        if nodes > limits.xml_nodes or len(stack)+1 > limits.xml_depth:
            raise ValidationError("XML_NODE_OR_DEPTH_LIMIT")
        if len(attrs) > limits.xml_attributes or any(
            len(k.encode()) + len(v.encode()) > limits.xml_attribute_bytes for k,v in attrs.items()):
            raise ValidationError("XML_ATTRIBUTE_LIMIT")
        parent = stack[-1][1] if stack else None
        in_meta = any(frame[1] == "meta" for frame in stack)
        if not stack:
            if tag != "annotations":
                raise ValidationError("UNSUPPORTED_XML_ROOT")
        elif not in_meta:
            allowed = {"annotations":{"version","meta","image"}, "image":{"box","tag"},
                       "box":{"attribute"}, "tag":{"attribute"}, "attribute":set(), "version":set()}
            if tag not in allowed.get(parent,set()):
                raise ValidationError("UNSUPPORTED_CVAT_RECORD")
        if not in_meta and tag in counts:
            counts[tag] += 1
            maximum = {"image":limits.annotation_images,"box":limits.boxes,"tag":limits.tags}[tag]
            if counts[tag] > maximum:
                raise ValidationError("XML_RECORD_LIMIT")
            if tag == "image":
                try:
                    if not attrs.get("name") or not attrs.get("id") or int(attrs["width"]) < 0 or int(attrs["height"]) < 0:
                        raise ValueError()
                except (KeyError,ValueError):
                    raise ValidationError("INVALID_CVAT_IMAGE") from None
                if attrs["id"] in image_ids:
                    raise ValidationError("DUPLICATE_CVAT_IMAGE_ID")
                image_ids.add(attrs["id"])
            if tag == "box" and not all(k in attrs for k in ("label","xtl","ytl","xbr","ybr")):
                raise ValidationError("INVALID_CVAT_BOX")
            # Raw coordinates are preserved, including geometry QC outliers; no
            # replacement scoring/thresholding or silently omitted records.
        database.execute("INSERT INTO xml_nodes VALUES (?,?,?,?,?)",
                         (nodes,stack[-1][0] if stack else None,tag,json.dumps(attrs,ensure_ascii=False),""))
        stack.append([nodes,tag,[],0])

    def text(value):
        if stack:
            stack[-1][3] += len(value.encode())
            if stack[-1][3] > limits.xml_text_bytes:
                raise ValidationError("XML_TEXT_LIMIT")
            stack[-1][2].append(value)

    def end(tag):
        node, _, pieces, _ = stack.pop()
        database.execute("UPDATE xml_nodes SET text=? WHERE id=?",("".join(pieces),node))

    parser.StartElementHandler, parser.CharacterDataHandler, parser.EndElementHandler = start,text,end
    size = 0
    try:
        while block := file.read(limits.block_bytes):
            size += len(block)
            if size > limits.xml_bytes:
                raise ValidationError("XML_SIZE_LIMIT")
            parser.Parse(block,False)
        parser.Parse(b"",True)
    except expat.ExpatError:
        raise ValidationError("MALFORMED_XML") from None
    if counts["image"] == 0:
        raise ValidationError("UNSUPPORTED_CVAT_NO_IMAGES")
    return {**counts,"nodes":nodes}
