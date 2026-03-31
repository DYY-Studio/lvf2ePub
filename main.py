import re
import csv
import sqlite3
import zipfile
import shutil
import argparse
from lxml import etree
from pathlib import Path
from bs4 import BeautifulSoup, Doctype

EPUB_CONTAINER = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>'''

pathReplace = re.compile(r'[\\/:*?"<>|]')

def getLegalPath(rawPath: str) -> str:

    replacedPath = rawPath

    def getFullwidth(char: str) -> str:
        if len(char) != 1: return char
        if not ord(char) in range(0x20, 0x80):
            return char
        else:
            return chr(ord(char) - 0x20 + 0xFF00)

    for m in re.finditer(pathReplace, rawPath):
        replacedPath = replacedPath[:m.start()] + getFullwidth(m.group()) + replacedPath[m.end():]
    
    return replacedPath

class LVFConverter:
    def __init__(self):
        # 基础标签映射表
        self.tag_map = {
            's': 'html',
            't': 'div',
            'f': 'span',
            'r': 'ruby',
            'z': 'rb',
            'a': 'rt',
            'b': 'br',
            'u': 'head', # 这里的 <u> 通常包含元数据
            'd': 'title'
        }

    @staticmethod
    def generate_epub3_nav(toc_data: list[tuple[int, str, str]], title: str):
        # 1. 定义命名空间
        NSMAP = {
            None: "http://www.w3.org/1999/xhtml",
            "epub": "http://www.idpf.org/2007/ops"
        }
        
        # 2. 初始化基础结构
        root = etree.Element("html", nsmap=NSMAP)
        head = etree.SubElement(root, "head")
        etree.SubElement(head, "title").text = title
        
        body = etree.SubElement(root, "body")
        nav = etree.SubElement(body, "nav", {f"{{{NSMAP['epub']}}}type": "toc", "id": "toc"})
        etree.SubElement(nav, "h1").text = title
        
        # 初始化栈：存储当前的 <ol> 元素
        # 初始层级为 0，栈顶是根目录的 <ol>
        current_ol = etree.SubElement(nav, "ol")
        stack = [(0, current_ol)] 
        
        for level, title, href in toc_data:
            # 获取当前栈顶的层级
            last_level, last_ol = stack[-1]
            
            if level > last_level:
                # 层级加深：在最后一个 li 中创建新的 ol
                # 如果是第一次运行，last_ol 就是初始的 ol
                if last_ol.getchildren():
                    last_li = last_ol.getchildren()[-1]
                    new_ol = etree.SubElement(last_li, "ol")
                else:
                    new_ol = last_ol
                
                stack.append((level, new_ol))
                target_ol = new_ol
                
            elif level < last_level:
                # 层级回退：弹出栈直到找到对应的父层级
                while stack and stack[-1][0] > level:
                    stack.pop()
                target_ol = stack[-1][1]
            else:
                # 层级相同
                target_ol = last_ol

            # 添加列表项
            li = etree.SubElement(target_ol, "li")
            a = etree.SubElement(li, "a", href=href)
            a.text = title

        # 3. 导出字符串
        return etree.tostring(root, pretty_print=True, encoding='utf-8', xml_declaration=True, doctype='<!DOCTYPE html>')

    def generate_epub(self, lvfpath: Path, outdir: Path):
        self.orig_file_map = dict()
        self.navigation_map = dict()
        self.lvfpath = lvfpath = Path(lvfpath)

        if (orig_file_map_csv := lvfpath / 'original_size_file_list.csv').exists():
            with open(orig_file_map_csv) as csvfile:
                for row in csv.reader(csvfile):
                    self.orig_file_map[row[0]] = row[1]
        
        if (navigation := lvfpath / 'kjroot.db').exists():
            conn = sqlite3.connect(navigation)
            cur = conn.execute('''
                SELECT content_list_id, caption, link, hierarchy 
                FROM tbl_contentlist 
                ORDER BY content_list_id ASC
            ''')
            for result in cur.fetchall():
                self.navigation_map[int(result[2].lstrip('PG'))] = (result[1], result[3])
            cur.close()
            conn.close()
        else:
            raise FileNotFoundError("Cannot find kjroot.db")

        self.manifest_map = dict()
        self.spine_map = dict()

        self.title = lvfpath.with_suffix('').name
        toc_text = ''

        opfpath = lvfpath / 'standard.opf'
        if not opfpath.exists():
            opfpath = lvfpath / 'content.opf'
        if opfpath.exists():
            with open(opfpath, mode='r', encoding='utf-8') as opff:
                bs = BeautifulSoup(opff, 'xml')
                manifest = bs.find('manifest')
                for item in manifest.find_all('item'):
                    # 由于难以还原的内联标签，选择抛弃所有的样式文件
                    if item['media-type'] == 'text/css': 
                        item.decompose()
                        continue
                    self.manifest_map[item['id']] = item['href']

                self.title = bs.find('dc:title').text

                spine = bs.find('spine')
                toc_list = []
                for idx, itemref in enumerate(spine.find_all('itemref')):
                    self.spine_map[f'C{idx:>05d}.xml'] = (href := self.manifest_map[itemref['idref']])
                    if (caption := self.navigation_map.get(idx)):
                        toc_list.append((caption[1], caption[0], href))
                
                toc_text = LVFConverter.generate_epub3_nav(toc_list, self.title)

                bs.package.metadata["xmlns:opf"] = "http://www.idpf.org/2007/opf"
                    
                # 保存修改后的 OPF
                target_OEBPS = Path(outdir) / getLegalPath(self.title) / 'OEBPS'
                new_opf_path = target_OEBPS / 'content.opf'
                target_OEBPS.mkdir(parents=True, exist_ok=True)
                with open(new_opf_path, 'w', encoding='utf-8') as f:
                    f.write(f"<?xml version='1.0' encoding='utf-8'?>\n{bs.decode_contents()}")

                with open(target_OEBPS / self.manifest_map['toc'], mode='wb') as f:
                    f.write(toc_text)
        else:
            raise FileNotFoundError('Cannot find standard.opf')

        self.targetdir = targetdir = Path(outdir) / getLegalPath(self.title)
        
        for k, v in self.spine_map.items():
            infile: Path = lvfpath / 'o' / k
            if not infile.exists():
                raise ValueError(f'Cannot find "{k}"')
            
            outfile: Path = targetdir / 'OEBPS' / v

            outfile.parent.mkdir(parents=True, exist_ok=True)
            self.convert_file(infile, outfile)

        (metainf := targetdir / 'META-INF').mkdir(parents=True, exist_ok=True)
        with open(metainf / 'container.xml', mode='w', encoding='utf-8') as f:
            f.write(EPUB_CONTAINER) 

        with zipfile.ZipFile(targetdir.parent / targetdir.with_suffix('.epub').name, mode='w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            zf.writestr('mimetype', 'application/epub+zip'.encode('utf-8'), compress_type=zipfile.ZIP_STORED)
            for fpath in targetdir.glob('**/*'):
                zf.write(fpath, fpath.relative_to(targetdir))

        shutil.rmtree(targetdir)

        return str(targetdir.parent / targetdir.with_suffix('.epub').name)

    def parse_s_attribute(self, s_str: str) -> tuple[str, str, str, dict[str, str]]:
        """解析 S 属性中的内联样式和原始类名"""
        if not s_str:
            return None, None, None, None
        
        orig_attrs = {item[0].strip(): item[1].strip() for item in re.findall(r'-xepub-([^:]+):\s*([^;]+);', s_str)}

        # 提取原始 class
        # class_match = re.search(r'-xepub-class:\s*([^;]+);', s_str)
        orig_class = orig_attrs.get('class')
        
        # 提取原始 src (针对图片)
        # src_match = re.search(r'-xepub-src:\s*([^;]+);', s_str)
        orig_src = orig_attrs.get('src')
        
        # 清理掉 -xepub- 开头的私有属性，保留标准 CSS
        clean_style = re.sub(r'-xepub-[^:]+:[^;]+;', '', s_str).strip()
        
        return orig_class, clean_style, orig_src, orig_attrs

    def convert_file(self, input_path: str | Path, output_path: str | Path):
        with open(input_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'xml')

        # 1. 彻底删除所有 <gen> 标签（冗余的降维显示数据）
        for gen in soup.find_all('gen'):
            gen.decompose()

        # 2. 递归处理所有标签
        for tag in soup.find_all():
            # 获取原始标签名 (O 属性优先级最高)
            orig_name = tag.get('O')
            if not orig_name:
                orig_name = self.tag_map.get(tag.name, tag.name)
            
            # 处理样式和类名
            s_attr = tag.get('S')
            orig_class, clean_style, orig_src, orig_attrs = self.parse_s_attribute(s_attr)

            # 更新标签名
            tag.name = orig_name

            # 更新属性
            if orig_class:
                tag['class'] = orig_class
            if clean_style:
                tag['style'] = clean_style
            
            # 处理图片路径还原
            if tag.name == 'img':
                # 优先使用 -xepub-src 里的逻辑路径，如果没有则保留 BVA 物理路径
                now_src = tag.get('s')
                tag['src'] = orig_src if orig_src else now_src

                if orig_attrs and (alt := orig_attrs.get('alt-value')):
                    tag['alt'] = ''.join([chr(int(c.strip(), 16)) for c in alt.split(',')])

                # 处理图像重命名
                if orig_src:
                    target = output_path.parent / orig_src
                    target.parent.mkdir(parents=True, exist_ok=True)

                    if now_src and (absolute_now_src := self.lvfpath / now_src).exists():
                        if (orig_size_file := self.orig_file_map.get(now_src)):
                            shutil.copy(self.lvfpath / orig_size_file, target)
                        else:
                            shutil.copy(absolute_now_src, target)

                # 删除多余的 BVA 属性
                for attr in ['s', 'd', 'e', 't', 'o']:
                    if tag.has_attr(attr): del tag[attr]
            
            # 处理外字 (Gaiji)
            if tag.name == 'e':
                # 检查是否可以通过 unicode 码位还原
                if tag.get('s') == 'unicode' and tag.get('c'):
                    try:
                        # 将十六进制字符串转换为整数，再转为 Unicode 字符
                        char_code = int(tag.get('c'), 16)
                        original_char = chr(char_code)
                        
                        # 直接用纯文本替换掉整个 <e> 标签
                        tag.replace_with(original_char)
                        continue # 标签已被替换，跳过后续属性清理逻辑
                    except (ValueError, TypeError):
                        # 如果转换失败，则走下面的图片回退逻辑
                        pass

                # 回退逻辑：如果无法还原为 Unicode，则仍转为 img 标签
                tag.name = 'img'
                tag['class'] = 'gaiji'
                tag['src'] = tag.get('i') 
                tag['alt'] = tag.get('a', '〓')
                # 清理多余属性
                for attr in ['a', 'c', 'i', 's', 't', 'v']:
                    if tag.has_attr(attr): del tag[attr]
            # 移除已处理的私有属性
            if tag.has_attr('O'): del tag['O']
            if tag.has_attr('S'): del tag['S']

        # nested_ps = soup.select('p p')
        # for p in nested_ps:
        #     p.name = 'span'

        for parent_p in soup.select('p:has(p)'):
            parent_p.name = 'div'

        # 3. 构造标准的 XHTML 包装
        head = soup.find('head')
        html = soup.find('html')
        body = soup.find('body')

        standard_html_tag = soup.new_tag('html', attrs={
            "xmlns": "http://www.w3.org/1999/xhtml",
            "xmlns:epub": "http://www.idpf.org/2007/ops"
        })

        if head:
            head.decompose()

        if not html:
            soup.wrap(standard_html_tag)
        else:
            soup.html['xmlns'] = "http://www.w3.org/1999/xhtml"
            soup.html['xmlns:epub'] = "http://www.idpf.org/2007/ops"

        if not body:
            soup.html.name = 'body'
            del soup.body['xmlns']
            del soup.body['xmlns:epub']
            soup.body.wrap(standard_html_tag)
        else:
            if not body.parent.name == 'html':
                for parent in body.parents:
                    if parent.parent and parent.parent.name == 'html':
                        orig_body = body.unwrap() 
                        parent.wrap(orig_body)
                        break

        soup.html.insert(0, soup.new_tag('head'))
        soup.html.head.append(soup.new_tag('title'))
        soup.html.head.title.string = ''
        soup.insert(0, Doctype('html'))

        xhtml_content = soup.prettify()

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xhtml_content)

# 使用示例
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'lvf_dir', 
        help='Dir of uncompressed LVF. Required files: /kjroot.db, /standard.opf (content.opf)',
        type=Path
    )
    parser.add_argument(
        'output_dir', 
        help='Where to output ePub',
        type=Path
    )
    args = parser.parse_args()

    converter = LVFConverter()
    print(f'Done: "{converter.generate_epub(args.lvf_dir, args.output_dir)}"')