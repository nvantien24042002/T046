"""Dynamic class/slice coverage; targets are user-defined, never inferred as truth."""
import json
import itertools
import pandas as pd

DIMS=('class','weather','timeofday','occluded')

def build_universe(images, boxes):
    """One row per valid object, with image-level scene attributes."""
    if images.empty:
        return pd.DataFrame(columns=['image_id',*DIMS,'box_id'])
    im=images.copy()
    for c in ('weather','timeofday'):
        im[c]=im[c].fillna('missing').astype(str).str.strip().replace('', 'missing')
    if boxes.empty:
        return pd.DataFrame(columns=['image_id',*DIMS,'box_id'])
    ob=boxes[boxes.valid_geometry.fillna(False)].copy()
    if ob.empty:return pd.DataFrame(columns=['image_id',*DIMS,'box_id'])
    ob['class']=ob.label.astype(str)
    ob['occluded']=ob.occluded.astype(str).map(lambda x:'yes' if x.lower() in ('1','true','yes') else 'no' if x.lower() in ('0','false','no') else 'unknown')
    return ob.merge(im[['image_id','weather','timeofday']],on='image_id',how='left',validate='many_to_one')

def choices(images,boxes):
    result={}
    for d in DIMS:
        if d=='class': vals=boxes.label.dropna().astype(str).unique().tolist() if not boxes.empty else []
        elif d=='occluded': vals=['yes','no','unknown']
        else: vals=images[d].dropna().astype(str).unique().tolist() if not images.empty else []
        result[d]=sorted(set(vals))
    return result

def count_support(universe,images,filters,unit='images'):
    """Return support and number of images with unresolved metadata for this slice."""
    if not filters:return 0,0
    sub=universe
    for d,v in filters.items():
        sub=sub[sub[d].fillna('missing').astype(str)==str(v)]
    support=sub.image_id.nunique() if unit=='images' else len(sub)
    unresolved=0
    for d,v in filters.items():
        if d in ('weather','timeofday') and v not in ('missing','unknown'):
            unresolved+=int(images[d].fillna('missing').astype(str).isin(['missing','unknown']).sum())
    return int(support),int(unresolved)

def evaluate_targets(images,boxes,targets):
    universe=build_universe(images,boxes)
    records=[]
    for t in targets:
        filters=t['filters'];unit=t.get('unit','images');target=int(t['target']);weight=float(t.get('weight',1))
        if not filters or target<=0 or weight<=0 or unit not in ('images','objects') or any(k not in DIMS for k in filters):
            raise ValueError('Slice phải có filter, target > 0, weight > 0 và unit hợp lệ.')
        support,unresolved=count_support(universe,images,filters,unit)
        gap=max(0,target-support)
        records.append(dict(slice=t.get('name') or ' × '.join(f'{k}={v}' for k,v in filters.items()),filters=json.dumps(filters,ensure_ascii=False),unit=unit,support=support,target=target,gap=gap,attainment_pct=round(100*support/target,1),weight=weight,weighted_gap=round(gap/target*weight,4),status='Đạt' if gap==0 else 'Thiếu',metadata_unresolved=unresolved,confidence='Tạm thời' if unresolved else 'Quan sát'))
    return pd.DataFrame(records)

def recommendations(evaluated):
    if evaluated.empty:return pd.DataFrame()
    out=evaluated[evaluated.gap>0].sort_values(['weighted_gap','gap'],ascending=False).copy()
    out['recommendation']=out.apply(lambda r: ('Kiểm tra và bổ sung metadata trước khi kết luận thiếu; sau đó ưu tiên thu thập/gán nhãn ' if r.metadata_unresolved else 'Ưu tiên bổ sung/kiểm tra annotation ') + f"{r['slice']} (thiếu {r.gap} {r.unit}).",axis=1)
    return out

def generate_targets(images,boxes,dimensions,unit,target,weight=1,include_unobserved=False,cap=250):
    """Generate editable initial targets from detected values; optional Cartesian combos."""
    vals=choices(images,boxes)
    if not dimensions:return []
    if include_unobserved:
        combos=itertools.product(*(vals[d] for d in dimensions))
    else:
        universe=build_universe(images,boxes)
        combos=universe[dimensions].drop_duplicates().itertuples(index=False,name=None) if not universe.empty else []
    result=[]
    for combo in combos:
        if len(result)>=cap:break
        f=dict(zip(dimensions,combo))
        result.append(dict(name=' × '.join(f'{k}={v}' for k,v in f.items()),filters=f,unit=unit,target=int(target),weight=float(weight)))
    return result
