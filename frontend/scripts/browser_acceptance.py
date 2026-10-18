"""Real browser/backend acceptance; run only against the isolated backend runner."""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ['N205B_BACKEND_SOURCE'])
from tests.fixtures import make_zip

out = Path('.cache/acceptance'); out.mkdir(parents=True, exist_ok=True)
pages = ['overview', 'quality', 'labels', 'distribution', 'review', 'reports', 'coverage', 'scene-finder']
results = []
with tempfile.TemporaryDirectory(prefix='n205b-fe-fixture-') as folder, sync_playwright() as pw:
    fixture = make_zip(Path(folder) / 'private-upload.zip')
    browser = pw.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width':1440, 'height':1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    responses = []
    def capture(response):
        if '/api/browser/v1/' in response.url:
            responses.append({'url':response.url.split('?')[0], 'status':response.status})
    page.on('response', capture)
    page.goto('http://127.0.0.1:5173/upload'); page.wait_for_load_state('networkidle')
    page.locator('input[type=file]').set_input_files(str(fixture))
    page.get_by_role('button', name='Bắt đầu tải lên & Phân tích', exact=True).click()
    page.wait_for_function("document.querySelector('.upload-result-card') || document.querySelector('.dataset-uploader-card [role=alert]')", timeout=60000)
    assert page.locator('.dataset-uploader-card [role=alert]').count() == 0, page.locator('main').inner_text()
    expect(page.get_by_text('✓ Tải lên và nạp dữ liệu thành công!')).to_be_visible()
    for width in [1440,1024,768,375]:
        page.set_viewport_size({'width':width,'height':1000})
        for route in pages:
            # Navigate through the SPA to retain the selected dataset/session.
            if width <= 800:
                toggle = page.get_by_role('button', name='Menu điều hướng')
                if toggle.get_attribute('aria-expanded') == 'false': toggle.click()
            page.locator(f'nav a[href="/{route}"]').click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('main h1')).to_be_visible()
            expect(page.get_by_text('Đang hiển thị kết quả của bộ dữ liệu đã chọn.')).to_be_visible()
            assert page.locator('main [role=alert]').count() == 0, page.locator('main').inner_text()
            expect(page.locator(f'[data-page="{route}"][data-state]')).not_to_have_attribute('data-state', 'loading', timeout=30000)
            expect(page.locator('main table, main .image-gallery, main textarea').first).to_be_visible(timeout=30000)
            page.wait_for_load_state('networkidle')
            overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
            assert not overflow, f'page overflow {route} at {width}'
            text = page.locator('body').inner_text()
            assert 'flat.png' not in text and 'texture.png' not in text and 'private-upload.zip' not in text
            assert '/storage/' not in text and 'image_path' not in text
            for attr in ['alt','title','aria-label']:
                values = page.locator(f'[{attr}]').evaluate_all('(els, attr) => els.map(e => e.getAttribute(attr))',attr)
                assert not any('flat.png' in v or 'texture.png' in v or '/storage/' in v for v in values)
            if route in ['review','scene-finder']:
                buttons = page.get_by_role('button', name='Mở ảnh xem trước')
                expect(buttons.first).to_be_visible()
                buttons.first.click()
                expect(page.get_by_role('dialog')).to_be_visible()
                img = page.get_by_role('dialog').locator('img')
                expect(img).to_be_visible()
                expect(img).to_have_js_property('complete',True)
                assert img.evaluate('(e) => e.naturalWidth > 0')
                assert page.get_by_role('dialog').evaluate('(e) => e.getBoundingClientRect().width <= innerWidth')
                page.keyboard.press('Escape')
                expect(page.get_by_role('dialog')).to_have_count(0)
            page.screenshot(path=str(out/f'{route}-{width}.png'), full_page=True)
            if route in ['overview', 'review'] and width in [1440,375]:
                page.screenshot(path=str(out/f'{route}-{width}-viewport.png'))
            results.append({'page':route,'width':width,'overflow':overflow,'private_text':False,'state':'backend-ready'})
    # A real server CSV request, with no fabricated client-side data.
    page.set_viewport_size({'width':1440,'height':1000})
    page.locator('nav a[href="/reports"]').click();page.wait_for_load_state('networkidle')
    with page.expect_download() as download:
        page.get_by_role('button',name='Tải Image Quality CSV').click()
    assert download.value.suggested_filename == 'dataset-export.csv'
    # Real empty/unsupported response: no explicit coverage targets are silently invented.
    page.locator('nav a[href="/coverage"]').click();page.wait_for_load_state('networkidle')
    expect(page.get_by_role('textbox', name='Targets do người dùng cung cấp (JSON)')).to_be_visible()
    # Browser response failure must show retry, without retaining old metrics.
    page.route('**/api/browser/v1/datasets/*/dashboard',lambda route:route.fulfill(status=503,body='{}',content_type='application/json'))
    page.locator('nav a[href="/quality"]').click();page.wait_for_load_state('networkidle')
    expect(page.get_by_role('alert')).to_be_visible()
    expect(page.get_by_role('button',name='Thử lại API')).to_be_visible()
    assert page.locator('.overview-metrics').count()==0
    page.unroute('**/api/browser/v1/datasets/*/dashboard')
    page.get_by_role('button',name='Thử lại API').click();page.wait_for_load_state('networkidle')
    expect(page.locator('.overview-metrics')).to_be_visible()
    # Real empty filter response, followed by a second dataset and explicit selection switches.
    page.get_by_label('Trạng thái ảnh', exact=True).select_option('PASS')
    expect(page.get_by_text('Không có ảnh khớp bộ lọc.', exact=True)).to_be_visible()
    expect(page.get_by_role('group',name='Ảnh trong bộ lọc').locator('dd')).to_have_text('0')
    page.get_by_label('Trạng thái ảnh', exact=True).select_option('')
    expect(page.get_by_role('group',name='Ảnh trong bộ lọc').locator('dd')).to_have_text('2')
    second=Path(folder)/'another-private-upload.zip'
    with zipfile.ZipFile(fixture) as source, zipfile.ZipFile(second,'w') as destination:
        destination.writestr('images/flat.png',source.read('images/flat.png'))
    page.locator('nav a[href="/upload"]').click()
    page.locator('input[type=file]').set_input_files(str(second))
    page.get_by_role('button',name='Bắt đầu tải lên & Phân tích',exact=True).click()
    expect(page.get_by_text('✓ Tải lên và nạp dữ liệu thành công!')).to_be_visible(timeout=60000)
    page.locator('nav a[href="/overview"]').click()
    page.wait_for_load_state('networkidle')
    expect(page.get_by_role('group',name='Ảnh trong archive').locator('dd')).to_have_text('1', timeout=30000)
    page.get_by_role('button',name='Bộ dữ liệu 1 · ready',exact=True).click()
    page.wait_for_load_state('networkidle')
    expect(page.get_by_role('group',name='Ảnh trong archive').locator('dd')).to_have_text('2', timeout=30000)
    page.get_by_role('button',name='Bộ dữ liệu 2 · ready',exact=True).click()
    page.wait_for_load_state('networkidle')
    expect(page.get_by_role('group',name='Ảnh trong archive').locator('dd')).to_have_text('1', timeout=30000)
    assert 'another-private-upload.zip' not in page.locator('body').inner_text()
    assert not errors, errors
    (out/'browser-results.json').write_text(json.dumps({'checks':results,'responses':responses,'page_errors':errors,'csv_download':True,'retry':True,'empty_filter':True,'dataset_switch':True},ensure_ascii=False,indent=2),encoding='utf-8')
    browser.close()
print('PASS: 32 real-backend page/viewport checks; preview modal, CSV, empty filters, retry and dataset switching verified.')
