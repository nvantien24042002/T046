import json
import pandas as pd
import plotly.express as px
import streamlit as st
from core.coverage_dynamic import DIMS,choices,evaluate_targets,recommendations,generate_targets,build_universe

def render_coverage(images,boxes,dataset_signature):
    st.subheader('Coverage theo class & slice — cấu hình động')
    st.caption('Coverage quan sát từ annotation; không có GT. Target do Leader đặt. Các slice có thể giao nhau nên không cộng gap giữa các slice.')
    if images.empty:
        st.info('Cần annotations.xml có thẻ image để tính coverage.')
        return
    if st.session_state.get('coverage_dataset_signature')!=dataset_signature:
        st.session_state.coverage_dataset_signature=dataset_signature
        st.session_state.coverage_targets=[]
    universe=build_universe(images,boxes)
    st.markdown('#### 1. Phân bố quan sát theo class')
    n=len(images)
    if not universe.empty:
        cls=universe.groupby('class').agg(objects=('box_id','size'),images=('image_id','nunique')).reset_index()
        cls['image_coverage_pct']=(100*cls.images/n).round(2)
        cls['object_share_pct']=(100*cls.objects/len(universe)).round(2)
        st.dataframe(cls,hide_index=True,use_container_width=True)
        st.plotly_chart(px.bar(cls,x='class',y='image_coverage_pct',title='Tỷ lệ ảnh có class (%)'),use_container_width=True)
    else:st.info('Không có box hợp lệ; class coverage bằng 0 hoặc chưa xác định.')
    st.markdown('#### 2. Thiết lập mục tiêu')
    opts=choices(images,boxes)
    mode=st.radio('Cách cấu hình', ['Thêm một slice','Tạo nhiều slice tự động','Nhập JSON'],horizontal=True)
    if mode=='Thêm một slice':
        selected=st.multiselect('Chiều phân tích',list(DIMS),default=['class'] if opts['class'] else [],key='cov_dims')
        filters={}
        cols=st.columns(2)
        for i,d in enumerate(selected):
            if opts[d]:
                with cols[i%2]:filters[d]=st.selectbox(d,opts[d],key=f'cov_{d}')
        a,b,c=st.columns(3)
        with a:unit=st.selectbox('Đơn vị',['images','objects'],key='cov_unit')
        with b:target=st.number_input('Mục tiêu tối thiểu',min_value=1,value=100,key='cov_target')
        with c:weight=st.number_input('Trọng số ưu tiên',min_value=0.01,value=1.0,step=0.5,key='cov_weight')
        if st.button('Thêm slice',disabled=not filters):
            name=' × '.join(f'{k}={v}' for k,v in filters.items())
            st.session_state.coverage_targets.append(dict(name=name,filters=filters,unit=unit,target=int(target),weight=float(weight)))
    elif mode=='Tạo nhiều slice tự động':
        selected=st.multiselect('Kết hợp chiều',list(DIMS),default=['class'] if opts['class'] else [],key='cov_auto_dims')
        include=st.checkbox('Bao gồm tổ hợp chưa xuất hiện (Cartesian product)',value=False)
        a,b,c=st.columns(3)
        with a:unit=st.selectbox('Đơn vị',['images','objects'],key='cov_auto_unit')
        with b:target=st.number_input('Mục tiêu cho mỗi slice',min_value=1,value=100,key='cov_auto_target')
        with c:weight=st.number_input('Trọng số',min_value=0.01,value=1.0,key='cov_auto_weight')
        if st.button('Tạo slice',disabled=not selected):
            generated=generate_targets(images,boxes,selected,unit,target,weight,include)
            st.session_state.coverage_targets=generated
            st.success(f'Đã tạo {len(generated)} slice (giới hạn 250). Bạn có thể chỉnh từng target ở bảng dưới.')
    else:
        sample=[dict(name='bus-night-rain',filters={'class':'bus','timeofday':'night','weather':'rain'},unit='images',target=100,weight=2)]
        raw=st.text_area('JSON danh sách target',value=json.dumps(st.session_state.coverage_targets or sample,ensure_ascii=False,indent=2),height=200)
        if st.button('Áp dụng JSON'):
            try:
                items=json.loads(raw)
                if not isinstance(items,list):raise ValueError('Cần JSON array')
                evaluate_targets(images,boxes,items)
                st.session_state.coverage_targets=items
                st.success('Đã áp dụng.')
            except (ValueError,KeyError,TypeError) as exc:st.error(str(exc))
    targets=st.session_state.coverage_targets
    if not targets:
        st.info('Chưa có mục tiêu. Hãy thêm slice hoặc tạo tự động để so sánh.')
        return
    st.markdown('#### 3. Chỉnh mục tiêu theo từng slice')
    table=pd.DataFrame([{**t,'filters':json.dumps(t['filters'],ensure_ascii=False)} for t in targets])
    edited=st.data_editor(table,hide_index=True,use_container_width=True,num_rows='dynamic',key='cov_editor')
    if st.button('Lưu chỉnh sửa mục tiêu'):
        try:
            parsed=[]
            for row in edited.to_dict('records'):
                f=json.loads(row['filters']) if isinstance(row['filters'],str) else row['filters']
                parsed.append(dict(name=row['name'],filters=f,unit=row['unit'],target=int(row['target']),weight=float(row['weight'])))
            evaluate_targets(images,boxes,parsed)
            st.session_state.coverage_targets=parsed
            st.rerun()
        except (ValueError,TypeError,KeyError) as exc:st.error(f'Mục tiêu không hợp lệ: {exc}')
    try: result=evaluate_targets(images,boxes,targets)
    except (ValueError,KeyError,TypeError) as exc:
        st.error(f'Không tính được coverage: {exc}');return
    st.markdown('#### 4. So sánh thực tế với mục tiêu')
    a,b,c,d=st.columns(4)
    a.metric('Tổng slice',len(result))
    b.metric('Slice đạt',int((result.gap==0).sum()))
    c.metric('Slice thiếu',int((result.gap>0).sum()))
    d.metric('Weighted coverage',f"{100*result.loc[result.gap==0,'weight'].sum()/result.weight.sum():.1f}%")
    st.dataframe(result.drop(columns=['filters']),hide_index=True,use_container_width=True)
    plot=result.melt(id_vars='slice',value_vars=['support','target'],var_name='Loại',value_name='Số lượng')
    st.plotly_chart(px.bar(plot,x='slice',y='Số lượng',color='Loại',barmode='group',title='Observed vs Target (xem đơn vị trong bảng)'),use_container_width=True)
    st.markdown('#### 5. Đề xuất slice cần bổ sung')
    rec=recommendations(result)
    if rec.empty:st.success('Các slice đã cấu hình đều đạt mục tiêu quan sát.')
    else:
        st.dataframe(rec[['slice','gap','unit','weight','metadata_unresolved','confidence','recommendation']],hide_index=True,use_container_width=True)
        st.warning('Các đề xuất là ưu tiên review/thu thập dữ liệu, không chứng minh dataset sai hoặc annotation thiếu.')
    st.download_button('Tải coverage CSV',result.to_csv(index=False).encode('utf-8-sig'),'coverage_results.csv','text/csv')
    st.download_button('Tải recommendations CSV',rec.to_csv(index=False).encode('utf-8-sig'),'slice_recommendations.csv','text/csv')
    st.download_button('Tải cấu hình targets JSON',json.dumps(targets,ensure_ascii=False,indent=2).encode('utf-8'),'coverage_targets.json','application/json')
