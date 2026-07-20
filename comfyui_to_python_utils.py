import os
from typing import Sequence, Mapping, Any, Union
import sys

sys.path.append('../')

args = None
has_manager = False

def import_custom_nodes() -> None:
    """Find all custom nodes in the custom_nodes folder and add those node objects to NODE_CLASS_MAPPINGS

    This function sets up a new asyncio event loop, initializes the PromptServer,
    creates a PromptQueue, and initializes the custom nodes.
    """
    if has_manager:
        try:
            import manager_core as manager
        except ImportError:
            print("Could not import manager_core, proceeding without it.")
            return
        else:
            if hasattr(manager, "get_config"):
                print("Patching manager_core.get_config to enforce offline mode.")
                try:
                    get_config = manager.get_config
                    def _get_config(*args, **kwargs):
                        config = get_config(*args, **kwargs)
                        config["network_mode"] = "offline"
                        return config
                    manager.get_config = _get_config
                except Exception as e:
                    print("Failed to patch manager_core.get_config:", e)

    import asyncio
    import execution
    from nodes import init_extra_nodes
    import server

    # Creating a new event loop and setting it as the default loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def inner():
        # Creating an instance of PromptServer with the loop
        server_instance = server.PromptServer(loop)
        execution.PromptQueue(server_instance)

        # Initializing custom nodes
        await init_extra_nodes(init_custom_nodes=True)
    
    loop.run_until_complete(inner())


def find_path(name: str, path: str = None) -> str:
    """
    Recursively looks at parent folders starting from the given path until it finds the given name. 
    Returns the path as a Path object if found, or None otherwise.
    """
    # If no path is given, use the current working directory
    if path is None:
        if args is None or args.comfyui_directory is None:
            path = os.getcwd()
        else:
            path = args.comfyui_directory
    
    # Check if the current directory contains the name
    if name in os.listdir(path):
        path_name = os.path.join(path, name)
        print(f"{name} found: {path_name}")
        return path_name

    # Get the parent directory
    parent_directory = os.path.dirname(path)

    # If the parent directory is the same as the current directory, we've reached the root and stop the search
    if parent_directory == path:
        return None

    # Recursively call the function with the parent directory
    return find_path(name, parent_directory)


def add_comfyui_directory_to_sys_path() -> None:
    """
    Add 'ComfyUI' to the sys.path
    """
    comfyui_path = find_path('ComfyUI')
    if comfyui_path is not None and os.path.isdir(comfyui_path):
        sys.path.append(comfyui_path)

        manager_path = os.path.join(comfyui_path, "custom_nodes", "ComfyUI-Manager", "glob")

        if os.path.isdir(manager_path) and os.listdir(manager_path):
            sys.path.append(manager_path)
            global has_manager
            has_manager = True
        
        import __main__

        if getattr(__main__, "__file__", None) is None:
            __main__.__file__ = os.path.join(comfyui_path, "main.py")
        
        print(f"'{comfyui_path}' added to sys.path")


def add_extra_model_paths() -> None:
    """
    Parse the optional extra_model_paths.yaml file and add the parsed paths to the sys.path.
    """
    from comfy.options import enable_args_parsing
    enable_args_parsing()
    from utils.extra_config import load_extra_path_config


    extra_model_paths = find_path("extra_model_paths.yaml")
    
    if extra_model_paths is not None:
        load_extra_path_config(extra_model_paths)
    else:
        print("Could not find the extra_model_paths config file.")
    


def get_value_at_index(obj: Union[Sequence, Mapping], index: int) -> Any:
    """Returns the value at the given index of a sequence or mapping.

    If the object is a sequence (like list or string), returns the value at the given index.
    If the object is a mapping (like a dictionary), returns the value at the index-th key.

    Some return a dictionary, in these cases, we look for the "results" key
    
    Args:
        obj (Union[Sequence, Mapping]): The object to retrieve the value from.
        index (int): The index of the value to retrieve.
    
    Returns:
        Any: The value at the given index.
    
    Raises:
        IndexError: If the index is out of bounds for the object and the object is not a mapping.
    """
    try:
        return obj[index]
    except KeyError:
        return obj['result'][index]

def parse_arg(s: Any, default: Any = None) -> Any:
    """ Parses a JSON string, returning it unchanged if the parsing fails. """
    if __name__ != "__main__" or not isinstance(s, str):
        return s
    
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s

def gen_noise(seed_type: str, seed: int, seed_key: str) -> int:
    import random

    if "_seeds" not in globals():
        global _seeds
        _seeds = {}

    if seed_type == "random":
        return random.randint(1, 2**64)
    elif seed_type == "fixed":
        return seed
    elif seed_type == "increment":
        _seeds[seed_key] = _seeds.get(seed_key, seed) + 1
        return _seeds[seed_key]
    elif seed_type == "decement":
        _seeds[seed_key] = _seeds.get(seed_key, seed) - 1
        return _seeds[seed_key]
    else:
        raise ValueError(f"Unknown seed_type: {seed_type}")


def save_image_wrapper(context, cls):
    if args.output is None: return cls
    
    from PIL import Image, ImageOps, ImageSequence
    from PIL.PngImagePlugin import PngInfo

    import numpy as np

    class WrappedSaveImage(cls):
        counter = 0

        def save_images(self, images, filename_prefix="ComfyUI", prompt=None, extra_pnginfo=None):
            if args.output is None:
                return super().save_images(images, filename_prefix, prompt, extra_pnginfo)
            else:
                if len(images) > 1 and args.output == "-":
                    raise ValueError("Cannot save multiple images to stdout")
                filename_prefix += self.prefix_append
                
                results = list()
                for (batch_number, image) in enumerate(images):
                    i = 255. * image.cpu().numpy()
                    img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
                    metadata = None
                    if not args.disable_metadata:
                        metadata = PngInfo()
                        if prompt is not None:
                            metadata.add_text("prompt", json.dumps(prompt))
                        if extra_pnginfo is not None:
                            for x in extra_pnginfo:
                                metadata.add_text(x, json.dumps(extra_pnginfo[x]))
                    
                    if args.output == "-":
                        # Hack to briefly restore stdout
                        if context is not None:
                            context.__exit__(None, None, None) 
                        try:
                            img.save(sys.stdout.buffer, format="png", pnginfo=metadata, compress_level=self.compress_level)
                        finally:
                            if context is not None:
                                context.__enter__()
                    else:
                        subfolder = ""
                        if len(images) == 1:
                            if os.path.isdir(args.output):
                                subfolder = args.output
                                file = "output.png"
                            else:
                                subfolder, file = os.path.split(args.output)
                                if subfolder == "":
                                    subfolder = os.getcwd()
                        else:
                            if os.path.isdir(args.output):
                                subfolder = args.output
                                file = filename_prefix
                            else:
                                subfolder, file = os.path.split(args.output)
                            
                            if subfolder == "":
                                subfolder = os.getcwd()
                            
                            files = os.listdir(subfolder)
                            file_pattern = file
                            while True:
                                filename_with_batch_num = file_pattern.replace("%batch_num%", str(batch_number))
                                file = f"{filename_with_batch_num}_{self.counter:05}.png"
                                self.counter += 1

                                if file not in files:
                                    break
                        
                        img.save(os.path.join(subfolder, file), pnginfo=metadata, compress_level=self.compress_level)
                        print("Saved image to", os.path.join(subfolder, file))
                        results.append({
                            "filename": file,
                            "subfolder": subfolder,
                            "type": self.type
                        })

                return {"ui": {"images": results}}

    return WrappedSaveImage

def resolve_save_metadata(prompt_data):
    """Returns the (prompt, extra_pnginfo) a save node would have received from the server.

    The generated script only carries the API prompt, so extra_pnginfo (which holds the
    UI workflow) is never available.
    """
    if args is not None and args.disable_metadata:
        return None, None
    return prompt_data, None

def save_encoded_data(context, datas, filename_prefix, extension, suffix=""):
    """Routes already-encoded outputs to their destination.

    `datas` holds one encoded bytes object per batch item. With --output the bytes go
    straight there (stdout, an exact file path, or a directory); without it they are
    written to the ComfyUI output directory the way the node itself would have.
    Returns the result dicts describing what was written.
    """
    import folder_paths

    output = None if args is None else args.output

    if output == "-":
        if len(datas) > 1:
            raise ValueError("Cannot save multiple outputs to stdout")
        for data in datas:
            # Hack to briefly restore stdout
            if context is not None:
                context.__exit__(None, None, None)
            try:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            finally:
                if context is not None:
                    context.__enter__()
        return []

    # An exact file path is the only case where the node's filename_prefix is ignored
    if output is not None and not os.path.isdir(output):
        target = os.path.abspath(output)
        directory = os.path.dirname(target) or os.getcwd()
        os.makedirs(directory, exist_ok=True)
        stem, target_ext = os.path.splitext(target)
        results = []
        for batch_number, data in enumerate(datas):
            if len(datas) == 1:
                path = target
            else:
                path = f"{stem}_{batch_number:05}{target_ext or ('.' + extension)}"
            with open(path, "wb") as f:
                f.write(data)
            print("Saved output to", path)
            results.append({
                "filename": os.path.basename(path),
                "subfolder": os.path.dirname(path),
                "type": "output"
            })
        return results

    root = folder_paths.get_output_directory() if output is None else os.path.abspath(output)
    full_output_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(filename_prefix, root)
    os.makedirs(full_output_folder, exist_ok=True)

    results = []
    for batch_number, data in enumerate(datas):
        name = filename.replace("%batch_num%", str(batch_number))
        file = f"{name}_{counter:05}{suffix}.{extension}"
        with open(os.path.join(full_output_folder, file), "wb") as f:
            f.write(data)
        print("Saved output to", os.path.join(full_output_folder, file))
        results.append({"filename": file, "subfolder": subfolder, "type": "output"})
        counter += 1

    return results

def encode_audio_data(audio, format="flac", quality="128k", metadata=None):
    """Encodes each batch item of an AUDIO input to `format`, in memory.

    Mirrors comfy_api's AudioSaveHelper.save_audio, but yields the encoded bytes
    instead of writing them to disk.
    """
    from io import BytesIO
    import av

    opus_rates = [8000, 12000, 16000, 24000, 48000]

    datas = []
    for waveform in audio["waveform"].cpu():
        sample_rate = audio["sample_rate"]

        # Opus only accepts a fixed set of sample rates
        if format == "opus":
            if sample_rate > 48000:
                sample_rate = 48000
            elif sample_rate not in opus_rates:
                for rate in sorted(opus_rates):
                    if rate > sample_rate:
                        sample_rate = rate
                        break
                if sample_rate not in opus_rates:
                    sample_rate = 48000

            if sample_rate != audio["sample_rate"]:
                import torchaudio
                waveform = torchaudio.functional.resample(waveform, audio["sample_rate"], sample_rate)

        buffer = BytesIO()
        container = av.open(buffer, mode="w", format=format)

        for key, value in (metadata or {}).items():
            container.metadata[key] = value

        layout = "mono" if waveform.shape[0] == 1 else "stereo"
        if format == "opus":
            stream = container.add_stream("libopus", rate=sample_rate, layout=layout)
            if quality.endswith("k"):
                stream.bit_rate = int(quality[:-1]) * 1000
        elif format == "mp3":
            stream = container.add_stream("libmp3lame", rate=sample_rate, layout=layout)
            if quality == "V0":
                stream.codec_context.qscale = 1
            elif quality.endswith("k"):
                stream.bit_rate = int(quality[:-1]) * 1000
        else:  # flac
            stream = container.add_stream("flac", rate=sample_rate, layout=layout)

        frame = av.AudioFrame.from_ndarray(
            waveform.movedim(0, 1).reshape(1, -1).float().numpy(),
            format="flt",
            layout=layout,
        )
        frame.sample_rate = sample_rate
        frame.pts = 0
        container.mux(stream.encode(frame))
        container.mux(stream.encode(None))
        container.close()

        datas.append(buffer.getvalue())

    return datas

def audio_metadata_dict(prompt_data):
    """Container metadata for the audio encoders (values must be strings)."""
    prompt, extra_pnginfo = resolve_save_metadata(prompt_data)
    metadata = {}
    if prompt is not None:
        metadata["prompt"] = json.dumps(prompt)
    if extra_pnginfo is not None:
        for x in extra_pnginfo:
            metadata[x] = json.dumps(extra_pnginfo[x])
    return metadata

def save_image_advanced_wrapper(context, cls, prompt_data=None):
    """SaveImageAdvanced: encodes PNG/EXR bytes in memory, then routes them."""

    class WrappedSaveImageAdvanced(cls):
        @classmethod
        def execute(cls, images, filename_prefix, format, **kwargs):
            from comfy_api.latest import io as comfy_io
            from comfy_extras.nodes_images import _encode_image, inject_png_metadata, inject_exr_metadata

            file_format = format["format"]
            bit_depth = format["bit_depth"]
            colorspace = format.get("input_color_space", "sRGB")
            prompt, extra_pnginfo = resolve_save_metadata(prompt_data)

            datas = []
            for image in images:
                encoded = _encode_image(image, file_format, bit_depth, colorspace)
                if prompt is not None or extra_pnginfo is not None:
                    if file_format == "png":
                        encoded = inject_png_metadata(encoded, prompt, extra_pnginfo)
                    elif file_format == "exr":
                        encoded = inject_exr_metadata(encoded, prompt, extra_pnginfo, colorspace)
                datas.append(encoded)

            results = save_encoded_data(context, datas, filename_prefix, file_format)
            return comfy_io.NodeOutput(images, ui={"images": results})

    return WrappedSaveImageAdvanced

def save_audio_wrapper(context, cls, prompt_data=None):
    """SaveAudio (FLAC): encodes in memory, then routes the bytes."""

    class WrappedSaveAudio(cls):
        @classmethod
        def execute(cls, audio, filename_prefix="ComfyUI", format="flac", **kwargs):
            from comfy_api.latest import io as comfy_io

            if audio is None:
                raise ValueError("SaveAudio: input audio is None (source video may have no audio track).")
            datas = encode_audio_data(audio, format=format, metadata=audio_metadata_dict(prompt_data))
            results = save_encoded_data(context, datas, filename_prefix, format)
            return comfy_io.NodeOutput(audio, ui={"audio": results})

    return WrappedSaveAudio

def save_audio_mp3_wrapper(context, cls, prompt_data=None):
    """SaveAudioMP3: encodes in memory, then routes the bytes."""

    class WrappedSaveAudioMP3(cls):
        @classmethod
        def execute(cls, audio, filename_prefix="ComfyUI", format="mp3", quality="128k", **kwargs):
            from comfy_api.latest import io as comfy_io

            if audio is None:
                raise ValueError("SaveAudioMP3: input audio is None (source video may have no audio track).")
            datas = encode_audio_data(audio, format=format, quality=quality, metadata=audio_metadata_dict(prompt_data))
            results = save_encoded_data(context, datas, filename_prefix, format)
            return comfy_io.NodeOutput(audio, ui={"audio": results})

    return WrappedSaveAudioMP3

def save_audio_opus_wrapper(context, cls, prompt_data=None):
    """SaveAudioOpus: encodes in memory, then routes the bytes."""

    class WrappedSaveAudioOpus(cls):
        @classmethod
        def execute(cls, audio, filename_prefix="ComfyUI", format="opus", quality="V3", **kwargs):
            from comfy_api.latest import io as comfy_io

            if audio is None:
                raise ValueError("SaveAudioOpus: input audio is None (source video may have no audio track).")
            datas = encode_audio_data(audio, format=format, quality=quality, metadata=audio_metadata_dict(prompt_data))
            results = save_encoded_data(context, datas, filename_prefix, format)
            return comfy_io.NodeOutput(audio, ui={"audio": results})

    return WrappedSaveAudioOpus

def save_audio_advanced_wrapper(context, cls, prompt_data=None):
    """SaveAudioAdvanced: format and quality arrive together as a dynamic combo dict."""

    class WrappedSaveAudioAdvanced(cls):
        @classmethod
        def execute(cls, audio, filename_prefix, format, **kwargs):
            from comfy_api.latest import io as comfy_io

            file_format = format.get("format", "flac")
            quality = format.get("quality", None)
            extra = {} if quality is None else {"quality": quality}
            datas = encode_audio_data(audio, format=file_format, metadata=audio_metadata_dict(prompt_data), **extra)
            results = save_encoded_data(context, datas, filename_prefix, file_format)
            return comfy_io.NodeOutput(audio, ui={"audio": results})

    return WrappedSaveAudioAdvanced

def save_video_wrapper(context, cls, prompt_data=None):
    """SaveVideo: muxes into a BytesIO buffer rather than a path."""

    class WrappedSaveVideo(cls):
        @classmethod
        def execute(cls, video, filename_prefix, format, codec, **kwargs):
            from io import BytesIO
            from comfy_api.latest import io as comfy_io, Types

            prompt, extra_pnginfo = resolve_save_metadata(prompt_data)
            metadata = {}
            if extra_pnginfo is not None:
                metadata.update(extra_pnginfo)
            if prompt is not None:
                metadata["prompt"] = prompt
            saved_metadata = metadata if len(metadata) > 0 else None

            buffer = BytesIO()
            video.save_to(
                buffer,
                format=Types.VideoContainer(format),
                codec=codec,
                metadata=saved_metadata,
            )

            extension = Types.VideoContainer.get_extension(format)
            results = save_encoded_data(context, [buffer.getvalue()], filename_prefix, extension, suffix="_")
            return comfy_io.NodeOutput(video, ui={"images": results, "animated": (True,)})

    return WrappedSaveVideo

def save_webm_wrapper(context, cls, prompt_data=None):
    """SaveWEBM: encodes the frames into a BytesIO buffer rather than a path."""

    class WrappedSaveWEBM(cls):
        @classmethod
        def execute(cls, images, codec, fps, filename_prefix, crf, **kwargs):
            from io import BytesIO
            from fractions import Fraction
            import av
            import torch
            from comfy_api.latest import io as comfy_io

            prompt, extra_pnginfo = resolve_save_metadata(prompt_data)

            buffer = BytesIO()
            container = av.open(buffer, mode="w", format="webm")

            if prompt is not None:
                container.metadata["prompt"] = json.dumps(prompt)
            if extra_pnginfo is not None:
                for x in extra_pnginfo:
                    container.metadata[x] = json.dumps(extra_pnginfo[x])

            # Save transparency when the images carry an alpha channel (RGBA) and the codec supports it.
            # vp9 -> yuva420p; other codecs have no usable alpha path, so the alpha is ignored.
            save_alpha = images.shape[-1] == 4 and codec == "vp9"

            codec_map = {"vp9": "libvpx-vp9", "av1": "libsvtav1"}
            stream = container.add_stream(codec_map[codec], rate=Fraction(round(fps * 1000), 1000))
            stream.width = images.shape[-2]
            stream.height = images.shape[-3]
            stream.pix_fmt = "yuva420p" if save_alpha else ("yuv420p10le" if codec == "av1" else "yuv420p")
            stream.bit_rate = 0
            stream.options = {"crf": str(crf)}
            if codec == "av1":
                stream.options["preset"] = "6"

            for frame in images:
                if save_alpha:
                    frame = av.VideoFrame.from_ndarray(torch.clamp(frame[..., :4] * 255, min=0, max=255).to(device=torch.device("cpu"), dtype=torch.uint8).numpy(), format="rgba")
                else:
                    frame = av.VideoFrame.from_ndarray(torch.clamp(frame[..., :3] * 255, min=0, max=255).to(device=torch.device("cpu"), dtype=torch.uint8).numpy(), format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            container.mux(stream.encode())
            container.close()

            results = save_encoded_data(context, [buffer.getvalue()], filename_prefix, "webm", suffix="_")
            return comfy_io.NodeOutput(images, ui={"images": results, "animated": (True,)})

    return WrappedSaveWEBM

