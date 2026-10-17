import io
import zipfile
from pathlib import PurePosixPath
import pandas as pd
import streamlit as st
from PIL import Image


def _norm(value):
    return str(value).replace(chr(92), '/').strip().lstrip('./').casefold()


def _value(value):
    return 'missing' if pd.isna(value) or not str(value).strip() else str(value).strip()


def _resolve(name, paths):
    key = _norm(name)
    exact = {_norm(p): p for p in paths}
    if key in exact:
        return exact[key]
    suffix = [p for p in paths if _norm(p).endswith('/' + key)]
    if len(suffix) == 1:
        return suffix[0]
    base = PurePosixPath(key).name
    matches = [p for p in paths if PurePosixPath(_norm(p)).name == base]
    return matches[0] if len(matches) == 1 else None


def render_scene_finder(images, zip_bytes, max_image_bytes=50*1024*1024):
    st.markdown('#### 🔎 Tìm ảnh theo thời tiết × thời điểm')
    st.caption('Lọc theo tag CVAT. unknown: đã gán nhưng chưa xác định; missing: chưa có metadata. Đây là danh sách cần kiểm tra, không tự kết luận gán nhãn sai.')
    if images is None or images.empty:
        st.info('Không có thông tin ảnh trong XML.')
        return
    df = images.copy()
    for key in ('weather','timeofday'):
        df[key] = df[key].map(_value)
    df['image_name'] = df['image_name'].fillna('').astype(str)
    c1,c2,c3 = st.columns([1,1,1.3])
    with c1:
        weather = st.multiselect('Weather', sorted(df.weather.unique(),key=lambda v:(v not in ('unknown','missing'),v)),placeholder='Tất cả',key='finder_weather')
    with c2:
        tod = st.multiselect('Time of day', sorted(df.timeofday.unique(),key=lambda v:(v not in ('unknown','missing'),v)),placeholder='Tất cả',key='finder_tod')
    with c3:
        keyword = st.text_input('Tên hoặc đường dẫn ảnh',placeholder='Tìm tên ảnh...',key='finder_name').strip()
    filtered = df
    if weather:filtered=filtered[filtered.weather.isin(weather)]
    if tod:filtered=filtered[filtered.timeofday.isin(tod)]
    if keyword:filtered=filtered[filtered.image_name.str.contains(keyword,case=False,regex=False,na=False)]
    filtered=filtered.reset_index(drop=True)
    a,b,c=st.columns(3)
    a.metric('Ảnh khớp bộ lọc',len(filtered))
    b.metric('Tổng ảnh trong XML',len(df))
    c.metric('Ảnh có metadata chưa rõ',int((df.weather.isin(['unknown','missing']) | df.timeofday.isin(['unknown','missing'])).sum()))
    if filtered.empty:
        st.info('Không tìm thấy ảnh khớp điều kiện.')
        return
    cols=['image_id','image_name','weather','timeofday']
    st.dataframe(filtered[cols],hide_index=True,use_container_width=True,height=min(410,80+35*len(filtered)))
    st.download_button('⬇️ Tải danh sách ảnh CSV',filtered[cols].to_csv(index=False).encode('utf-8-sig'),'scene_filtered_images.csv','text/csv',key='finder_csv')
    with st.expander('🖼️ Xem ảnh để kiểm tra',expanded=True):
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            paths=[f.filename for f in archive.infolist() if not f.is_dir() and f.filename.lower().endswith(('.jpg','.jpeg','.png','.bmp','.webp'))]
            if not paths:
                st.warning('ZIP chỉ có annotation, không có ảnh để xem trước.')
                return
            per_page=st.selectbox('Ảnh mỗi trang',[4,8,12,20],index=1,key='finder_page_size')
            max_page=(len(filtered)+per_page-1)//per_page
            page=st.number_input('Trang',min_value=1,max_value=max_page,value=1,key='finder_page')
            cells=st.columns(2)
            for i,(_,r) in enumerate(filtered.iloc[(page-1)*per_page:page*per_page].iterrows()):
                with cells[i%2]:
                    with st.container(border=True):
                        st.markdown('**'+r.image_name+'**')
                        st.caption('Weather: '+r.weather+' | Time of day: '+r.timeofday)
                        path=_resolve(r.image_name,paths)
                        if path is None:
                            st.warning('Không tìm thấy ảnh hoặc trùng tên ảnh trong ZIP.')
                            continue
                        if archive.getinfo(path).file_size>max_image_bytes:
                            st.warning('Ảnh lớn hơn 50 MB, không preview.')
                            continue
                        try:
                            with Image.open(io.BytesIO(archive.read(path))) as im:
                                st.image(im.copy(),use_container_width=True)
                        except Exception as exc:
                            st.warning('Không đọc được ảnh: '+str(exc))
