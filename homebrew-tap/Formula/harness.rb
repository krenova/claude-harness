class Harness < Formula
  desc "Claude Autonomous Harness - Run AI-powered development workflows on any project"
  homepage "https://github.com/yourusername/claude-autonomous-harness"
  url "https://files.pythonhosted.org/packages/claude-harness-0.1.0.tar.gz"
  sha256 "f6e8deea36f25a275e97740bef415d3ef02765e20bba689f51d4b5532e058052"
  license "MIT"
  version "0.1.0"

  depends_on "python@3.10" => :osx

  def install
    virtualenv_created_with_system_python = (ENV["PYTHONPATH"] || "").include?("site-packages")
    system python("-m", "venv", venv_root) unless virtualenv_created_with_system_python
    system "#{venv_root}/bin/pip", "install", "claude-harness"
  end

  def venv_root
    libexec/"venv"
  end

  test do
    system "#{libexec}/venv/bin/harness", "--version"
  end
end
