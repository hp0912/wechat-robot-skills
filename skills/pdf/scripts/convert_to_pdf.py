#!/usr/bin/env python3
"""Office to PDF using the preinstalled LibreOffice, isolated per invocation."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from _pdf_common import SkillArgumentParser, output_pdf, new_temp_pdf, publish_temp_file, run_cli

FORMATS={'.docx','.doc','.odt','.rtf','.pptx','.ppt','.odp','.xlsx','.xls','.ods'}


def convert(args):
    from pypdf import PdfReader
    source=Path(args.input).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in FORMATS or not 0 < source.stat().st_size <= 25*1024*1024:
        raise ValueError('输入需为不超过 25 MiB 的本地 Office 文档；不支持把 PDF 直接反向转成可编辑 Office')
    if not 1 <= args.timeout <= 600:
        raise ValueError('timeout 必须在 1–600 秒之间')
    executable=shutil.which('soffice') or shutil.which('libreoffice')
    if not executable:
        raise RuntimeError('基础镜像缺少 LibreOffice，需要更新镜像')
    target=output_pdf(args.output,args.overwrite)
    temporary=new_temp_pdf(target)
    try:
        with tempfile.TemporaryDirectory(prefix='pdf-office-') as folder:
            root=Path(folder)
            incoming=root/'input'; outgoing=root/'output'; profile=root/'profile'
            incoming.mkdir(); outgoing.mkdir(); profile.mkdir()
            local=incoming/('source'+source.suffix.lower()); shutil.copyfile(source,local)
            command=[executable,'-env:UserInstallation='+profile.as_uri(),'--headless','--nologo','--nodefault',
                     '--nofirststartwizard','--convert-to','pdf','--outdir',str(outgoing),str(local)]
            completed=subprocess.run(command,capture_output=True,text=True,timeout=args.timeout,check=False)
            result=outgoing/'source.pdf'
            if completed.returncode or not result.is_file():
                raise RuntimeError('Office 转 PDF 失败：'+(completed.stderr or completed.stdout)[-1500:])
            count=len(PdfReader(result).pages)
            if not count:
                raise ValueError('转换结果没有页面')
            shutil.copyfile(result,temporary)
        publish_temp_file(temporary,target,args.overwrite)
    finally:
        temporary.unlink(missing_ok=True)
    return {'source':str(source),'path':str(target),'page_count':count,'engine':'libreoffice','requires_visual_review':True,
            'note':'转换前需在对应 Office skill 中重算公式并核对字体、图表和打印范围。'}


def main(argv=None):
    parser=SkillArgumentParser(description='Office 文档导出 PDF，源文件保持不变')
    parser.add_argument('--input',required=True); parser.add_argument('--output',required=True)
    parser.add_argument('--timeout',type=int,default=180); parser.add_argument('--overwrite',action='store_true')
    return run_cli(lambda:convert(parser.parse_args(sys.argv[1:] if argv is None else argv)))


if __name__=='__main__':
    raise SystemExit(main())
