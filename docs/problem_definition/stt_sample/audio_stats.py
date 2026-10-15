import sys, numpy as np
x = np.fromfile(sys.argv[1], dtype=np.int16).astype(np.float32)/32768
sr=16000; fl=400; hop=160
n=(len(x)-fl)//hop
fr=np.stack([x[i*hop:i*hop+fl] for i in range(n)])
e=10*np.log10((fr**2).mean(1)+1e-10)
dig = e < -90
print(f"duration {len(x)/sr:.1f}s, digital-silence ratio {dig.mean()*100:.1f}%")
ns = e[~dig]
noise=np.percentile(ns,10); speech=np.percentile(ns,95)
print(f"within non-silent audio: noise p10 {noise:.1f} dB, speech p95 {speech:.1f} dB -> est SNR ~{speech-noise:.1f} dB")
act = (~dig) & (e > noise+6)
X=(np.abs(np.fft.rfft(fr[act]*np.hanning(fl),axis=1))**2).mean(0)
f=np.fft.rfftfreq(fl,1/sr); cum=np.cumsum(X)/X.sum()
print("energy 5%%..95%% band: %.0f-%.0f Hz" % (f[np.searchsorted(cum,0.05)], f[np.searchsorted(cum,0.95)]))
# segments of non-silence
segs=[];inside=False
for i,d in enumerate(dig):
    if not d and not inside: s=i; inside=True
    if d and inside: segs.append((s/100,i/100)); inside=False
if inside: segs.append((s/100,len(dig)/100))
segs=[s for s in segs if s[1]-s[0]>0.5]
print("non-silent blocks:", [(round(a,1),round(b,1)) for a,b in segs])
for a,b in segs:
    w=e[int(a*100):int(b*100)]
    print(f" {a:6.1f}-{b:6.1f}s  p10 {np.percentile(w,10):6.1f}  p95 {np.percentile(w,95):6.1f}  SNR~{np.percentile(w,95)-np.percentile(w,10):5.1f}")
print("clipped samples:", int((np.abs(x)>0.99).sum()))
