import io, zipfile, xml.etree.ElementTree as ET
from collections import Counter
import pandas as pd


def parse_distribution(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        paths=[p for p in z.namelist() if p.lower().endswith('annotations.xml')]
        if not paths: raise ValueError('ZIP không có annotations.xml')
        root=ET.fromstring(z.read(paths[0]))
    images=[]; boxes=[]; attrs=set()
    for im in root.findall('.//image'):
        iid=str(im.get('id','')); name=im.get('name',''); w=int(im.get('width','0')); h=int(im.get('height','0'))
        tags={}
        for tag in im.findall('tag'):
            key=tag.get('label','')
            val=tag.findtext('attribute') or tag.get('value')
            for a in tag.findall('attribute'):
                if a.get('name') in ('weather','timeofday'): tags[a.get('name')]=a.text
            if key in ('weather','timeofday') and val: tags[key]=val
            if key=='scene_info':
                for a in tag.findall('attribute'): tags[a.get('name','')]=a.text
        images.append(dict(image_id=iid,image_name=name,weather=tags.get('weather') or 'missing',timeofday=tags.get('timeofday') or 'missing'))
        for idx,b in enumerate(im.findall('box')):
            a={el.get('name',''):el.text for el in b.findall('attribute')}
            attrs.update(a)
            try:
                x1,y1,x2,y2=[float(b.get(k)) for k in ('xtl','ytl','xbr','ybr')]
                valid=(x2>x1 and y2>y1 and w>0 and h>0)
                area=100*(x2-x1)*(y2-y1)/(w*h) if valid else float('nan')
            except (ValueError,TypeError): valid=False;area=float('nan')
            boxes.append(dict(image_id=iid,image_name=name,box_id=f'{iid}:{idx}',label=b.get('label','unknown'),occluded=b.get('occluded','0'),valid_geometry=valid,area_pct=area,attributes=a))
    return pd.DataFrame(images,columns=['image_id','image_name','weather','timeofday']),pd.DataFrame(boxes,columns=['image_id','image_name','box_id','label','occluded','valid_geometry','area_pct','attributes']),sorted(attrs)

def class_distribution(boxes):
    if boxes.empty: return pd.DataFrame(columns=['class','boxes','images','share_pct'])
    out=boxes.groupby('label').agg(boxes=('box_id','size'),images=('image_name','nunique')).reset_index().rename(columns={'label':'class'})
    out['share_pct']=out.boxes/out.boxes.sum()*100
    return out.sort_values('boxes',ascending=False)

def imbalance_warnings(dist,min_count,min_share,max_share):
    if dist.empty:return dist.copy()
    return dist[(dist.boxes<min_count)|(dist.share_pct<min_share)|(dist.share_pct>max_share)].copy()

def attribute_distribution(images,boxes,attributes):
    records=[]
    for attr in attributes:
        for _,r in boxes.iterrows():
            val=r.attributes.get(attr) if isinstance(r.attributes,dict) else None
            records.append(dict(attribute=attr,value=val or 'missing',label=r.label))
    if not records:return pd.DataFrame(columns=['attribute','value','label','count'])
    return pd.DataFrame(records).groupby(['attribute','value','label'],dropna=False).size().reset_index(name='count')
