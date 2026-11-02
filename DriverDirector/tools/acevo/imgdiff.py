import os
import subprocess,sys,glob
FF=glob.glob(os.path.expanduser("~/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe"))[0]
def gray(p,w=86,h=36):
    return subprocess.run([FF,"-loglevel","error","-i",p,"-vf",f"scale={w}:{h},format=gray","-f","rawvideo","-"],capture_output=True,check=True).stdout
def diff(a,b):
    x,y=gray(a),gray(b); return sum(abs(i-j) for i,j in zip(x,y))/len(x)
if __name__=="__main__":
    print(round(diff(sys.argv[1],sys.argv[2]),2))
