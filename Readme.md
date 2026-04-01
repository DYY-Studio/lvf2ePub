# lvf2ePub

用于将基于ePub3标准，使用`epub2lvf2`生成的标准EBIX (LVF)文件还原为ePub3

与下列工具搭配使用：
* [EbixDumperFrida](https://github.com/DYY-Studio/EbixDumperFrida)

## 依赖
```shell
pip install bs4 lxml
```
## 使用方法
```shell
python main.py <lvf_path> <output_dir>
```
* `lvf_path`
  * 解压出来的LVF文件夹，或者是Zip格式压缩的LVF文件
  * 必须有下列文件：
    * `/kjroot.db` LVF的TOC文件
    * `/standard.opf` 标准ePub3 OPF
* `output_dir`
  * 输出文件夹

## 原理
在 epub2lvf 的过程中，基本所有关键信息均被保留

直接将其还原，并修复XML结构问题即可

## 兼容性测试
- [x] Apple Books
- [x] Janereader
- [x] calibre
- [x] Sigil ePubCheck

## 许可证
MIT