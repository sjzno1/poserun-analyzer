"""无依赖测试：stub 掉 docx/numpy/cv2，验证评分逻辑与报告结构（真实代码路径）"""
import sys, types, math

# ---- stub numpy ----
np = types.ModuleType('numpy')
class _Arr(list):
    pass
def _mean(x):
    x = list(x); return sum(x)/len(x) if x else 0.0
def _median(x):
    x = sorted(list(x)); n=len(x)
    return x[n//2] if n%2 else (x[n//2-1]+x[n//2])/2
def _std(x):
    x=list(x); m=_mean(x)
    return math.sqrt(sum((v-m)**2 for v in x)/len(x)) if x else 0.0
np.mean=_mean; np.median=_median; np.std=_std
np.clip=lambda a,lo,hi: max(lo,min(hi,a))
np.array=list
sys.modules['numpy']=np

# ---- stub docx + submodules ----
docx = types.ModuleType('docx')
class _Style:
    def __init__(self): self.font=types.SimpleNamespace(name=None,size=None); self.paragraph_format=types.SimpleNamespace(space_after=None); self.element=types.SimpleNamespace(rPr=types.SimpleNamespace(rFonts=types.SimpleNamespace(set=lambda *a:None)))
class _Doc:
    def __init__(self): self.styles={'Normal':_Style()}; self.sections=[]
    def add_heading(self,*a,**k): return types.SimpleNamespace(alignment=None)
    def add_paragraph(self,*a,**k): return types.SimpleNamespace(alignment=None,add_run=lambda *a,**k:types.SimpleNamespace(font=types.SimpleNamespace(size=None,color=types.SimpleNamespace(rgb=None),bold=None,italic=None)))
    def add_table(self,*a,**k):
        rows=[]
        class _C:
            def __init__(self): self._t=''; self.paragraphs=[types.SimpleNamespace(runs=[types.SimpleNamespace(font=types.SimpleNamespace(bold=None,color=types.SimpleNamespace(rgb=None)))])]
            @property
            def text(self): return self._t
            @text.setter
            def text(self,v): self._t=str(v)
        class _R:
            def __init__(self,n): self.cells=[_C() for _ in range(n)]
        class _T:
            pass
        t=_T(); t.rows=[_R(99) for _ in range(99)]; t.alignment=None
        return t
    def add_picture(self,*a,**k): return None
    def save(self,path):
        # 记录是否保存成功
        import os
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        open(path,'w').write('STUB-DOCX')
docx.Document=_Doc
docx.shared=types.SimpleNamespace(Pt=lambda x:x,Inches=lambda x:x,RGBColor=lambda *a:a,Cm=lambda x:x)
docx.enum=types.ModuleType('docx.enum')
docx.enum.text=types.SimpleNamespace(WD_ALIGN_PARAGRAPH=types.SimpleNamespace(CENTER='c'))
docx.enum.table=types.SimpleNamespace(WD_TABLE_ALIGNMENT=types.SimpleNamespace(CENTER='c'))
docx.oxml=types.ModuleType('docx.oxml')
docx.oxml.ns=types.SimpleNamespace(qn=lambda x:x)
sys.modules['docx']=docx
sys.modules['docx.shared']=docx.shared
sys.modules['docx.enum']=docx.enum
sys.modules['docx.enum.text']=docx.enum.text
sys.modules['docx.enum.table']=docx.enum.table
sys.modules['docx.oxml']=docx.oxml
sys.modules['docx.oxml.ns']=docx.oxml.ns

# ---- 现在导入真实模块 ----
import report_generator as rg

# 构造一组模拟分析结果（10 帧，轻微跨步、膝盖微弯、小腿略前倾）
sample = [
    {'knee_flexion':158.0,'shin_angle':74.0,'body_leang':7.0,
     'stride_analysis':{'stride_distance':0.06,'stride_level':'中度跨步'},
     'symmetry':{'symmetry_score':78,'symmetry_level':'良好'},
     'running_phase':'heel_strike'} for _ in range(10)
]

gen = rg.ReportGenerator()
metrics = gen._calculate_comprehensive_metrics(sample)
print('avg_knee=%.1f avg_shin=%.1f avg_leang=%.1f stride_dist=%.3f sym=%.1f hip_std=%.4f' % (
    metrics['avg_knee_flexion'],metrics['avg_shin_angle'],metrics['avg_body_leang'],
    metrics['stride_analysis']['stride_distance'],metrics['symmetry']['symmetry_score'],
    metrics['hip_stability']))
print('total=%s grade=%s' % (metrics['total'],metrics['grade']))
print('--- 逐项评分 ---')
for b in metrics['breakdown']:
    print('  %-8s %-12s %-18s %3d/100 w=%.2f' % (b['category'],b['item'],b['result'],b['score'],b['weight']))
print('problems:', metrics['problems'])
print('suggestions:', metrics['suggestions'])

# 权重合计应为 1.0
wsum = sum(b['weight'] for b in metrics['breakdown'])
assert abs(wsum-1.0) < 1e-6, f'权重合计={wsum}'
# 总分应在 0-100
assert 0 <= metrics['total'] <= 100
# 12 项
assert len(metrics['breakdown'])==12, len(metrics['breakdown'])

# 生成含截图的报告（用一个真实图片目录）
import os, tempfile, json
d = tempfile.mkdtemp()
# 造 3 张假截图 + info json
for i in range(1,4):
    open(os.path.join(d,f'keyframe_{i:02d}.jpg'),'wb').write(b'\xff\xd8\xff' + b'0'*50 + b'\xff\xd9')
    with open(os.path.join(d,f'keyframe_{i:02d}_info.json'),'w') as f:
        json.dump({'frame_number':i*10,'timestamp':'00:00.00',
                   'metrics':sample[0]}, f)
out = os.path.join(d,'test_report.docx')
p = gen.generate_report('test.mp4', {'name':'测试','coach':'系统'}, sample,
                        output_path=out, key_frames_dir=d)
assert os.path.exists(p), p
print('REPORT OK ->', os.path.basename(p))

# 极端场景：严重跨步 + 高跨步距离，分数应明显下降
bad = [
    {'knee_flexion':185.0,'shin_angle':55.0,'body_leang':1.0,
     'stride_analysis':{'stride_distance':0.18,'stride_level':'严重跨步'},
     'symmetry':{'symmetry_score':40,'symmetry_level':'较差'},
     'running_phase':'heel_strike'} for _ in range(10)
]
m2 = rg.ReportGenerator()._calculate_comprehensive_metrics(bad)
print('BAD CASE total=%s grade=%s (应明显低于正常)' % (m2['total'],m2['grade']))
assert m2['total'] < metrics['total'], 'bad case 分数应低于正常 case'

# 优秀场景
good = [
    {'knee_flexion':162.0,'shin_angle':85.0,'body_leang':8.0,
     'stride_analysis':{'stride_distance':0.03,'stride_level':'轻度跨步'},
     'symmetry':{'symmetry_score':92,'symmetry_level':'良好'},
     'running_phase':'toe_off'} for _ in range(10)
]
m3 = rg.ReportGenerator()._calculate_comprehensive_metrics(good)
print('GOOD CASE total=%s grade=%s' % (m3['total'],m3['grade']))
assert m3['total'] > metrics['total'] > m2['total'], '分数排序异常'

print('\nALL ASSERTIONS PASSED ✔')
