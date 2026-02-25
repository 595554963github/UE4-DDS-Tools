import argparse
import json
import os
import sys
import time
import threading
from contextlib import redirect_stdout
import concurrent.futures
import functools

try:
    import tkinter as tk
    from tkinter import filedialog, ttk, messagebox
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        DND_AVAILABLE = True
    except ImportError:
        DND_AVAILABLE = False
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

from util import (compare, get_ext, get_temp_dir,
                  get_file_list, get_base_folder, remove_quotes,
                  check_python_version, is_windows)
from unreal.uasset import Uasset, UASSET_EXT
from directx.dds import DDS
from directx.dxgi_format import DXGI_FORMAT
from directx.texconv import Texconv

TOOL_VERSION = "0.6.1"

UE_VERSIONS = ["4." + str(i) for i in range(28)] + ["5." + str(i) for i in range(5)] + ["ff7r", "borderlands3"]

UTEX_VERSIONS = [
    "5.4", "5.3", "5.2", "5.1", "5.0",
    "4.26 ~ 4.27", "4.24 ~ 4.25", "4.23", "4.20 ~ 4.22",
    "4.16 ~ 4.19", "4.15", "4.14", "4.12 ~ 4.13", "4.11", "4.10",
    "4.9", "4.8", "4.7", "4.4 ~ 4.6", "4.3", "4.0 ~ 4.2",
    "ff7r", "borderlands3"
]

TEXTURES = ["dds", "tga", "hdr"]
if is_windows():
    TEXTURES += ["bmp", "jpg", "png"]

IMAGE_FILTERS = ["point", "linear", "cubic"]

def create_gui():
    if not GUI_AVAILABLE:
        return None
    
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    
    root.title("UE4资源提取工具")
    root.geometry("1200x500")
    
    main_frame = ttk.Frame(root, padding="20")
    main_frame.pack(fill=tk.BOTH, expand=True)
    
    ttk.Label(main_frame, text="UE4资源提取工具", font=("", 14, "bold")).pack(pady=(0, 20))
    
    source_frame = ttk.Frame(main_frame)
    source_frame.pack(fill=tk.X, pady=5)
    ttk.Label(source_frame, text="源文件/文件夹:").pack(side=tk.LEFT, padx=(0, 10))
    source_var = tk.StringVar()
    source_entry = ttk.Entry(source_frame, textvariable=source_var, width=40)
    source_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
    
    def browse_source():
        path = filedialog.askdirectory(title="选择源文件夹")
        if path:
            source_var.set(path)
    
    ttk.Button(source_frame, text="浏览", command=browse_source, width=8).pack(side=tk.LEFT, padx=(5, 0))
    
    if DND_AVAILABLE:
        drag_label = ttk.Label(source_frame, text="拖放文件夹到此处", foreground="blue")
        drag_label.pack(side=tk.LEFT, padx=(10, 0))
        
        def on_drop(event):
            if event.data:
                path = event.data.strip('{}')
                if os.path.exists(path):
                    source_var.set(path)
        
        root.drop_target_register(DND_FILES)
        root.dnd_bind('<<Drop>>', on_drop)
    else:
        drag_label = ttk.Label(source_frame, text="(未安装tkinterdnd2，禁用拖放)", foreground="gray")
        drag_label.pack(side=tk.LEFT, padx=(10, 0))
    
    source_entry.bind("<Button-1>", lambda e: browse_source())
    drag_label.bind("<Button-1>", lambda e: browse_source())
    
    ttk.Label(main_frame, text="输出格式:").pack(anchor=tk.W, pady=(10, 5))
    format_var = tk.StringVar(value="dds")
    format_combo = ttk.Combobox(main_frame, textvariable=format_var, values=TEXTURES, state="readonly", width=20)
    format_combo.pack(anchor=tk.W, pady=(0, 10))
    
    ttk.Label(main_frame, text="UE版本:").pack(anchor=tk.W, pady=(5, 5))
    version_var = tk.StringVar(value="4.27")
    version_combo = ttk.Combobox(main_frame, textvariable=version_var, values=UE_VERSIONS, state="readonly", width=20)
    version_combo.pack(anchor=tk.W, pady=(0, 10))
    
    options_frame = ttk.Frame(main_frame)
    options_frame.pack(fill=tk.X, pady=10)
    
    no_mip_var = tk.BooleanVar(value=False)
    no_mip_check = ttk.Checkbutton(options_frame, text="禁用Mipmaps", variable=no_mip_var)
    no_mip_check.pack(side=tk.LEFT, padx=(0, 20))
    
    skip_var = tk.BooleanVar(value=True)
    skip_check = ttk.Checkbutton(options_frame, text="跳过非纹理资源", variable=skip_var)
    skip_check.pack(side=tk.LEFT)
    
    button_frame = ttk.Frame(main_frame)
    button_frame.pack(pady=20)
    
    result = {}
    processing = False
    extraction_thread = None
    extract_clicked = False
    
    def start_extract():
        nonlocal processing, extraction_thread, extract_clicked
        
        if not source_var.get():
            messagebox.showerror("错误", "请选择源文件夹！")
            return
        
        if processing:
            return
            
        processing = True
        extract_clicked = True
        
        result.clear()
        result.update({
            "file": source_var.get(),
            "mode": "export",
            "export_as": format_var.get(),
            "version": version_var.get(),
            "no_mipmaps": no_mip_var.get(),
            "skip_non_texture": skip_var.get(),
            "save_folder": "", 
            "texture": None,
            "force_uncompressed": False,
            "disable_tempfile": False,
            "image_filter": "linear",
            "max_workers": -1,
            "convert_to": "tga",
            "save_detected_version": False
        })
        
        def run_extraction():
            try:
                args_obj = argparse.Namespace()
                for key, value in result.items():
                    setattr(args_obj, key, value)
                
                config = get_config()
                fix_args(args_obj, config)
                
                print(f"开始提取: {args_obj.file}")
                print(f"输出格式: {args_obj.export_as}")
                print(f"UE版本: {args_obj.version}")
                print(f"输出到: {args_obj.file} (源文件所在目录)")
                
                main(args_obj, config)
                
                messagebox.showinfo("完成", f"提取完成！\n源路径: {args_obj.file}")
                
            except Exception as e:
                messagebox.showerror("错误", f"提取失败:\n{str(e)}")
            finally:
                nonlocal processing
                processing = False
        
        extraction_thread = threading.Thread(target=run_extraction)
        extraction_thread.daemon = True
        extraction_thread.start()
    
    def cancel():
        nonlocal processing, extraction_thread, result, extract_clicked
        if processing:
            if messagebox.askyesno("确认", "提取操作正在进行中，确定要退出吗？"):
                result.clear()
                extract_clicked = False
                root.destroy()
                return
        else:
            result.clear()
            extract_clicked = False
            root.destroy()
    
    ttk.Button(button_frame, text="开始提取", command=start_extract, width=15).pack(side=tk.LEFT, padx=10)
    ttk.Button(button_frame, text="退出", command=cancel, width=15).pack(side=tk.LEFT, padx=10)
    
    root.protocol("WM_DELETE_WINDOW", cancel)
    
    root.mainloop()
    
    return result if extract_clicked else None

def get_config():
    json_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(json_path):
        return {}
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)

def save_config(config):
    json_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def stdout_wrapper(func):
    @functools.wraps(func)
    def caller(*args, **kwargs):
        from io import StringIO
        import sys
        default_stdout = sys.stdout
        sys.stdout = StringIO()

        def flush(stdout):
            stdout.seek(0)
            print(stdout.read()[:-1], file=default_stdout, flush=True)
            sys.stdout = default_stdout

        try:
            response = func(*args, **kwargs)
        except Exception as e:
            flush(sys.stdout)
            raise e
        flush(sys.stdout)
        return response
    return caller

@stdout_wrapper
def parse(folder, file, args, texture_file=None):
    file = os.path.join(folder, file)
    if get_ext(file) == "dds":
        DDS.load(file, verbose=True)
    else:
        Uasset(file, version=args.version, verbose=True)

@stdout_wrapper
def valid(folder, file, args, version=None, texture_file=None):
    if version is None:
        version = args.version

    with get_temp_dir(disable_tempfile=args.disable_tempfile) as temp_dir:
        src_file = os.path.join(folder, file)
        new_file = os.path.join(temp_dir, file)

        if get_ext(file) == "dds":
            dds = DDS.load(src_file)
            dds.save(new_file)
            compare(src_file, new_file)

        else:
            asset = Uasset(src_file, version=version, verbose=True)
            old_name = asset.file_name
            asset.save(new_file, valid=True)
            new_name = asset.file_name
            for ext in UASSET_EXT:
                if (os.path.exists(f"{old_name}.{ext}") and (asset.has_textures() or ext == "uasset")):
                    compare(f"{old_name}.{ext}", f"{new_name}.{ext}")

def search_texture_file(file_base, ext_list, index=None, index2=None):
    if index is not None:
        file_base += index
    for ext in ext_list:
        file = file_base
        if index2 is not None and ext != "dds":
            file += index2
        file = ".".join([file, ext])
        if os.path.exists(file):
            return file
    raise RuntimeError(f"未找到纹理文件。({file_base})")

@stdout_wrapper
def inject(folder, file, args, texture_file=None):
    uasset_file = os.path.join(folder, file)
    asset = Uasset(uasset_file, version=args.version)

    if not asset.has_textures():
        desc = f"(文件:{uasset_file}, 类:{asset.get_main_class_name()})"
        if args.skip_non_texture:
            print("跳过了非纹理资源。" + desc)
            return
        raise RuntimeError("该uasset文件不包含纹理。" + desc)

    if texture_file is None:
        texture_file = args.texture
    file_base, ext = os.path.splitext(texture_file)
    ext = ext[1:].lower()
    if ext == "uasset":
        raise RuntimeError("无法将uasset文件注入到另一个uasset文件中。")
    if ext not in TEXTURES:
        raise RuntimeError(f"不支持的纹理格式。({ext})")

    textures = asset.get_texture_list()
    ext_list = [ext] + TEXTURES
    if len(textures) == 1:
        if textures[0].is_empty():
            src_files = [None]
        else:
            splitted = file_base.split("-")
            if len(splitted) >= 2 and splitted[-1] == "0":
                file_base = "-".join(splitted[:-1])
            index2 = "-0" if textures[0].is_array or textures[0].is_3d else None
            src_files = [search_texture_file(file_base, ext_list, index2=index2)]
    else:
        splitted = file_base.split(".")
        if len(splitted) >= 2 and (splitted[-1] == "0" or splitted[-1] == "0-0"):
            file_base = ".".join(splitted[:-1])
        src_files = []
        for i, tex in zip(range(len(textures)), textures):
            if tex.is_empty():
                src_files.append(None)
            index = f".{i}"
            index2 = "-0" if tex.is_array or tex.is_3d else None
            src_files.append(search_texture_file(file_base, ext_list, index=index, index2=index2))

    if any([(src is not None) and (get_ext(src) != "dds") for src in src_files]):
        texconv = Texconv()

    for tex, src in zip(textures, src_files):
        if tex.is_empty():
            print("跳过了空纹理。")
            continue

        if args.force_uncompressed:
            tex.to_uncompressed()

        if get_ext(src) == "dds":
            dds = DDS.load(src)
        else:
            if tex.dxgi_format > DXGI_FORMAT.get_max_canonical():
                print(f"警告:DDS转换器不支持{tex.dxgi_format.name}格式。"
                      "该纹理将使用未压缩格式。")
                tex.to_uncompressed()

            with get_temp_dir(disable_tempfile=args.disable_tempfile) as temp_dir:
                print(f"转换中:{src}")
                if tex.is_array or tex.is_3d:
                    src_base, src_ext = os.path.splitext(src)
                    src_base = src_base[:-2]
                    i = 0
                    dds_list = []
                    while True:
                        src = f"{src_base}-{i}{src_ext}"
                        if not os.path.exists(src):
                            break
                        temp_dds = texconv.convert_to_dds(src, tex.dxgi_format,
                                                          out=temp_dir, export_as_cubemap=tex.is_cube,
                                                          no_mip=len(tex.mipmaps) <= 1 or args.no_mipmaps,
                                                          image_filter=args.image_filter,
                                                          allow_slow_codec=True, verbose=False)
                        dds_list.append(DDS.load(temp_dds))
                        i += 1
                    dds = DDS.assemble(dds_list, is_array=tex.is_array)
                else:
                    temp_dds = texconv.convert_to_dds(src, tex.dxgi_format,
                                                      out=temp_dir, export_as_cubemap=tex.is_cube,
                                                      no_mip=len(tex.mipmaps) <= 1 or args.no_mipmaps,
                                                      image_filter=args.image_filter,
                                                      allow_slow_codec=True, verbose=False)
                    dds = DDS.load(temp_dds)

        tex.inject_dds(dds)
        if args.no_mipmaps:
            tex.remove_mipmaps()

    asset.update_package_source(is_official=False)
    new_file = os.path.join(args.save_folder, file)
    asset.save(new_file)

@stdout_wrapper
def export(folder, file, args, texture_file=None):
    src_file = os.path.join(folder, file)
    base_name = os.path.splitext(file)[0]
    
    if not args.save_folder:
        output_dir = os.path.dirname(src_file) if os.path.isfile(src_file) else src_file
    else:
        output_dir = args.save_folder
    
    new_dir = output_dir
    
    try:
        asset = Uasset(src_file, version=args.version)
    except Exception as e:
        print(f"加载文件失败{src_file}:{str(e)}")
        return

    if not asset.has_textures():
        desc = f"(文件:{src_file},类:{asset.get_main_class_name()})"
        if args.skip_non_texture:
            print(f"跳过了非纹理资源:{src_file}")
            return
        raise RuntimeError("该uasset文件不包含纹理。" + desc)

    textures = asset.get_texture_list()
    has_multi = len(textures) > 1
    if args.export_as != "dds":
        texconv = Texconv()

    texture_index = 1
    for tex, i in zip(textures, range(len(textures))):
        if tex.is_empty():
            print(f"跳过了空纹理:{src_file}")
            continue

        if args.no_mipmaps:
            tex.remove_mipmaps()

        try:
            dds = tex.get_dds()
        except Exception as e:
            print(f"提取纹理失败{src_file}:{str(e)}")
            continue
        if has_multi:
            file_name = os.path.join(new_dir, f"{base_name}_{texture_index}.dds")
            texture_index += 1
        else:
            file_name = os.path.join(new_dir, f"{base_name}.dds")
        
        if args.export_as == "dds":
            dds.save(file_name)
            print(f"已保存:{file_name}")
        elif dds.header.dxgi_format > DXGI_FORMAT.get_max_canonical():
            print(f"警告:DDS转换器不支持{dds.header.dxgi_format.name}格式。"
                  "该纹理将导出为DDS格式。")
            dds.save(file_name)
            print(f"已保存: {file_name}")
        else:
            with get_temp_dir(disable_tempfile=args.disable_tempfile) as temp_dir:
                temp_dds = os.path.join(temp_dir, os.path.basename(file_name))
                dds.save(temp_dds)
                converted_file = texconv.convert_dds_to(temp_dds, out=new_dir, fmt=args.export_as, verbose=False)
                print(f"已转换:{converted_file}")

@stdout_wrapper
def remove_mipmaps(folder, file, args, texture_file=None):
    src_file = os.path.join(folder, file)
    new_file = os.path.join(args.save_folder, file)
    asset = Uasset(src_file, version=args.version)
    textures = asset.get_texture_list()
    for tex in textures:
        if tex.is_empty():
            print("跳过了空纹理。")
            continue
        tex.remove_mipmaps()
    asset.save(new_file)

@stdout_wrapper
def copy(folder, file, args, texture_file=None):
    uasset_file = os.path.join(folder, file)
    asset = Uasset(uasset_file, version=args.version)
    if not asset.has_textures():
        print("跳过了非纹理资源。")
        return
    new_file = os.path.join(args.save_folder, file)
    asset.save(new_file)

@stdout_wrapper
def check_version(folder, file, args, texture_file=None):
    print("正在使用各个版本运行验证模式...")
    passed_version = []
    for ver in UTEX_VERSIONS:
        try:
            with redirect_stdout(open(os.devnull, "w")):
                valid(folder, file, args, ver.split(" ~ ")[0])
            print(f"  {(ver + ' ' * 11)[:11]}: 通过")
            passed_version.append(ver)
        except Exception:
            print(f"  {(ver + ' ' * 11)[:11]}: 失败")

    if len(passed_version) == 0:
        raise RuntimeError(
            "所有支持的版本均验证失败，无法使用本工具修改该资源。\n"
            f"({folder}/{file})")
    elif len(passed_version) == 1 and ("~" not in passed_version[0]):
        print(f"检测到版本为:{passed_version[0]}。")
    else:
        s = f"{passed_version}"[1:-1].replace("'", "")
        print(f"检测到多个可兼容的版本:({s})")

    passed_version = [ver.split(" ~ ")[0] for ver in passed_version]
    return passed_version

@stdout_wrapper
def convert(folder, file, args, texture_file=None):
    src_file = os.path.join(folder, file)
    new_file = os.path.join(args.save_folder, file)

    if args.convert_to.lower() in TEXTURES[1:]:
        ext = args.convert_to.lower()
    else:
        if not DXGI_FORMAT.is_valid_format(args.convert_to):
            raise RuntimeError(f"指定的格式未定义。({args.convert_to})")
        ext = "dds"

    new_file = os.path.splitext(new_file)[0] + "." + ext

    print(f"正在转换{src_file}到{new_file}...")

    texconv = Texconv()
    if ext == "dds":
        texconv.convert_to_dds(src_file, DXGI_FORMAT[args.convert_to],
                               out=os.path.dirname(new_file), export_as_cubemap=False,
                               no_mip=args.no_mipmaps,
                               image_filter=args.image_filter,
                               allow_slow_codec=True, verbose=False)
    elif get_ext(file) == "dds":
        texconv.convert_dds_to(src_file, out=os.path.dirname(new_file), fmt=args.convert_to, verbose=False)
    else:
        texconv.convert_nondds(src_file, out=os.path.dirname(new_file), fmt=args.convert_to, verbose=False)

MODE_FUNCTIONS = {
    "valid": valid,
    "inject": inject,
    "remove_mipmaps": remove_mipmaps,
    "parse": parse,
    "export": export,
    "check": check_version,
    "convert": convert,
    "copy": copy
}

def fix_args(args, config):
    if (args.version is None) and ("version" in config) and (config["version"] is not None):
        args.version = config["version"]

    if args.version is None:
        args.version = "4.27"

    if args.file.endswith(".txt"):
        with open(args.file, "r", encoding="utf-8") as f:
            args.file = remove_quotes(f.readline())

    if args.mode == "check":
        if isinstance(args.version, str):
            args.version = [args.version]
    else:
        if isinstance(args.version, list):
            args.version = args.version[0]

    if args.max_workers is not None and args.max_workers <= 0:
        args.max_workers = None

    if args.export_as == "hdr":
        args.export_as = "tga"

def print_args(args):
    mode = args.mode
    print("-" * 16)
    print(f"模式:{mode}")
    if mode != "check":
        print(f"UE版本:{args.version}")
    print(f"文件路径:{args.file}")
    if mode == "inject":
        print(f"纹理文件:{args.texture}")
    if mode not in ["check", "parse", "valid"]:
        print(f"保存文件夹:{args.save_folder}")
    if mode == "export":
        print(f"导出格式:{args.export_as}")
    if mode == "convert":
        print(f"转换目标格式:{args.convert_to}")
    if mode in ["inject", "export"]:
        print(f"禁用Mipmap:{args.no_mipmaps}")
        print(f"跳过非纹理资源:{args.skip_non_texture}")
    if mode == "inject":
        print(f"强制未压缩:{args.force_uncompressed}")
        print(f"图像过滤器:{args.image_filter}")
    with concurrent.futures.ProcessPoolExecutor(args.max_workers) as executor:
        print(f"最大工作线程数:{executor._max_workers}")
    print("-" * 16, flush=True)

def check_args(args):
    mode = args.mode
    if os.path.isfile(args.save_folder):
        raise RuntimeError(f"输出路径不是文件夹。({args.save_folder})")
    if args.file == "":
        raise RuntimeError("请指定uasset文件路径。")
    if not os.path.exists(args.file):
        raise RuntimeError(f"路径不存在。({args.file})")
    if mode == "inject":
        if args.texture is None or args.texture == "":
            raise RuntimeError("请指定纹理文件路径。")
        if os.path.isdir(args.file):
            if not os.path.isdir(args.texture):
                raise RuntimeError(
                    f"指定了文件夹作为uasset路径，但纹理路径不是文件夹。({args.texture})"
                )
        elif os.path.isdir(args.texture):
            raise RuntimeError(
                f"指定了文件作为uasset路径，但纹理路径是文件夹。({args.texture})"
            )
    if mode not in MODE_FUNCTIONS:
        raise RuntimeError(f"不支持的模式。({mode})")
    if mode != "check" and args.version not in UE_VERSIONS:
        raise RuntimeError(f"不支持的版本。({args.version})")
    if args.export_as not in TEXTURES:
        raise RuntimeError(f"不支持的导出格式。({args.export_as})")
    if args.image_filter.lower() not in IMAGE_FILTERS:
        raise RuntimeError(f"不支持的图像过滤器。({args.image_filter})")

def main(args, config={}):
    fix_args(args, config)
    print_args(args)
    check_args(args)

    mode = args.mode

    func = MODE_FUNCTIONS[mode]

    if os.path.isfile(args.file):
        file = args.file
        folder = os.path.dirname(file)
        file = os.path.basename(file)
        results = [func(folder, file, args)]
    else:
        if mode == "convert":
            ext_list = TEXTURES
        else:
            ext_list = ["uasset"]

        folder = args.file
        file_list = get_file_list(folder, ext=ext_list)
        texture_folder = args.texture

        if mode == "inject":
            texture_file_list = [os.path.join(texture_folder, file[:-6] + TEXTURES[0]) for file in file_list]
        else:
            texture_file_list = [None] * len(file_list)

        folder, base_folder = get_base_folder(folder)
        file_list = [os.path.join(base_folder, file) for file in file_list]

        with concurrent.futures.ProcessPoolExecutor(args.max_workers) as executor:
            futures = [
                executor.submit(func, folder, file, args, texture_file=texture)
                for file, texture in zip(file_list, texture_file_list)
            ]
            concurrent.futures.wait(futures)
            results = [future.result() for future in futures]

    if mode == "check" and args.save_detected_version:
        passed_versions = args.version
        for res in results:
            common = list(set(res) & set(passed_versions))
            if len(common) > 0:
                passed_versions = common
            else:
                passed_versions = res
        if len(passed_versions) == 1:
            passed_versions = passed_versions[0]
        config["version"] = passed_versions
        print(f"已将检测到的版本({passed_versions})保存到src/config.json", flush=True)
        save_config(config)

def run_with_gui():
    if not GUI_AVAILABLE:
        print("错误:无法导入GUI模块，请安装tkinter")
        sys.exit(1)
    
    gui_config = create_gui()
    
    if gui_config is None:
        print("操作已取消")
        sys.exit(0)
    
    if not gui_config or not gui_config.get("file"):
        print("操作已取消")
        sys.exit(0)
    
    args = argparse.Namespace()
    for key, value in gui_config.items():
        setattr(args, key, value)
    
    setattr(args, "texture", None)
    setattr(args, "save_detected_version", False)
    setattr(args, "convert_to", "tga")
    
    config = get_config()
    main(args, config)

if __name__ == "__main__":
    start_time = time.time()
    print(f"UE4资源提取工具 版本{TOOL_VERSION}")
    check_python_version(3, 10)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("使用方法:python main.py")
        print("启动图形界面")
    else:
        run_with_gui()
    
    print(f"运行时间(秒):{(time.time() - start_time)}")