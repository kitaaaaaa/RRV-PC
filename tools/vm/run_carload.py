import sys, struct, traceback; sys.path.insert(0,'J:/RIDGERACERV/tools/vm')
from machine import Machine, GP
def load_car(car,a1,a2,verbose=True):
    m=Machine('J:/RIDGERACERV/extracted/SLUS_200.02')
    m.attach_disc('J:/RIDGERACERV/RIDGERACERV.BIN')
    m.mem.write(GP-0x5870,4,0x368500)
    m.mem.write(GP-0x5afc,4,0); m.mem.write(GP-0x6ae0,4,0)
    for it in range(40):
        r=m.call(0x22c730,car,a1,a2)
        if verbose: print('iter',it,'ret',r,'state',struct.unpack_from('<i',m.mem.ram,GP-0x6ae0)[0],'cd',len(m.cd_log),'instr',m.ee.icount)
        if r==1: break
    return m
if __name__=='__main__':
    try:
        m=load_car(0,0,0)
    except Exception: traceback.print_exc(); sys.exit()
    rec=struct.unpack_from('<4I',m.mem.ram,0x1de6440); print('record',[hex(x) for x in rec])
    print('cd reads',m.cd_log); print('hw log',m.hw.log[:5],'captured',len(m.hw.captured))
